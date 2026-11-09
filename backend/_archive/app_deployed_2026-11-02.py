import json



import os



import re



import uuid



from datetime import datetime, timezone, timedelta



from urllib.parse import unquote



import boto3

from botocore.config import Config



from boto3.dynamodb.conditions import Attr



from botocore.exceptions import ClientError



TABLE_NAME = os.environ.get(



    "COMPLAINTS_TABLE",



    "CivicTrack-Complaints"



)

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")



LOCAL_MODE = os.environ.get("AWS_SAM_LOCAL") == "true"

# Synthetic identities are accepted only by SAM local when explicitly enabled.

# Never set ENABLE_LOCAL_DEMO_AUTH in a deployed environment.

LOCAL_DEMO_AUTH_ENABLED = (

    LOCAL_MODE and os.environ.get("ENABLE_LOCAL_DEMO_AUTH") == "true"

)



ALLOWED_ORIGIN = os.environ.get(



    "ALLOWED_ORIGIN",



    "http://localhost:5173"



)



# Local SeaweedFS settings. In AWS, use the Lambda execution role.

S3_BUCKET = os.environ.get("S3_BUCKET", "civictrack-evidence")

S3_INTERNAL_ENDPOINT_URL = os.environ.get(

    "S3_INTERNAL_ENDPOINT_URL",

    "http://host.docker.internal:8333" if LOCAL_MODE else None,

)

S3_PRESIGN_ENDPOINT_URL = os.environ.get(

    "S3_PRESIGN_ENDPOINT_URL",

    "http://localhost:8333" if LOCAL_MODE else None,

)

S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")

S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")

UPLOAD_URL_TTL_SECONDS = int(os.environ.get("UPLOAD_URL_TTL_SECONDS", "300"))

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024

ALLOWED_IMAGE_TYPES = {

    "image/jpeg": "jpg",

    "image/png": "png",

    "image/webp": "webp",

}



VALID_ROLES = {"Citizen", "Officer", "Admin"}



# --------------------------------------------------



# DATABASE



# --------------------------------------------------



def get_table():



    """Initialize DynamoDB when a database operation is required."""



    if LOCAL_MODE:



        dynamodb = boto3.resource(



            "dynamodb",



            endpoint_url="http://host.docker.internal:8000",



            region_name="ap-south-1",



            aws_access_key_id="dummy",



            aws_secret_access_key="dummy"



        )



    else:



        dynamodb = boto3.resource(



            "dynamodb",



            region_name=os.environ.get("AWS_REGION", "ap-south-1")



        )



    print(



        "DATABASE MODE:",



        "LOCAL" if LOCAL_MODE else "AWS",



        flush=True



    )



    print("TABLE NAME:", TABLE_NAME, flush=True)



    return dynamodb.Table(TABLE_NAME)



# --------------------------------------------------



# HTTP RESPONSE



# --------------------------------------------------



# --------------------------------------------------

# OBJECT STORAGE

# --------------------------------------------------



def get_s3_client(for_presign=False):

    """

    Create an S3 client.



    Lambda in Docker reaches SeaweedFS through host.docker.internal.

    The browser receives a presigned URL using localhost:8333.

    In AWS, the Lambda execution role supplies credentials automatically.

    """

    endpoint_url = (

        S3_PRESIGN_ENDPOINT_URL if for_presign else S3_INTERNAL_ENDPOINT_URL

    )



    client_args = {

        "region_name": os.environ.get("AWS_REGION", "us-east-1"),

        "config": Config(

            signature_version="s3v4",

            s3={"addressing_style": "path"},

        ),

    }



    if endpoint_url:

        client_args["endpoint_url"] = endpoint_url



    if LOCAL_MODE:

        if not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:

            raise RuntimeError(

                "Local S3 credentials are missing. Set S3_ACCESS_KEY_ID "

                "and S3_SECRET_ACCESS_KEY in the SAM local environment file."

            )

        client_args["aws_access_key_id"] = S3_ACCESS_KEY_ID

        client_args["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY



    return boto3.client("s3", **client_args)





def validate_object_key(value):

    """Reject URLs, absolute paths, traversal and control characters."""

    if not isinstance(value, str):

        return False

    value = value.strip()

    if not value or len(value) > 512:

        return False

    if value.startswith(("/", "\\\\")) or ".." in value.split("/"):

        return False

    if "://" in value or any(ord(char) < 32 for char in value):

        return False

    return True





def validate_uploaded_image(object_key, expected_prefix):

    """Confirm an uploaded image exists and meets the evidence constraints."""

    if not validate_object_key(object_key) or not object_key.startswith(expected_prefix):

        return False, "Invalid evidence object key"



    try:

        metadata = get_s3_client().head_object(

            Bucket=S3_BUCKET,

            Key=object_key,

        )

    except ClientError as error:

        code = error.response.get("Error", {}).get("Code", "")

        if code in ("404", "NoSuchKey", "NotFound"):

            return False, "Evidence photo has not been uploaded"

        print("S3 HEAD ERROR:", code, flush=True)

        raise



    content_type = metadata.get("ContentType", "")

    content_length = metadata.get("ContentLength", 0)

    if content_type not in ALLOWED_IMAGE_TYPES:

        return False, "Evidence must be a JPEG, PNG or WebP image"

    if content_length <= 0 or content_length > MAX_EVIDENCE_BYTES:

        return False, "Evidence image must be between 1 byte and 10 MB"

    return True, None





def create_presigned_upload_url(object_key, content_type):

    """Create a short-lived URL for a browser PUT upload."""

    client = get_s3_client(for_presign=True)

    return client.generate_presigned_url(

        "put_object",

        Params={

            "Bucket": S3_BUCKET,

            "Key": object_key,

            "ContentType": content_type,

        },

        ExpiresIn=UPLOAD_URL_TTL_SECONDS,

        HttpMethod="PUT",

    )





def parse_image_upload_request(event):

    try:

        body = json.loads(event.get("body") or "{}")

    except json.JSONDecodeError:

        return None, response(400, {"error": "Invalid JSON"})



    if not isinstance(body, dict):

        return None, response(400, {"error": "Invalid request body"})



    content_type = body.get("contentType")

    if content_type not in ALLOWED_IMAGE_TYPES:

        return None, response(

            400,

            {"error": "contentType must be image/jpeg, image/png or image/webp"},

        )

    return {"contentType": content_type}, None





def create_citizen_upload_url(event, user):

    """Issue an upload URL scoped to the authenticated citizen."""

    if user["role"] != "Citizen":

        return response(403, {"error": "Only citizens can upload complaint photos"})



    request, error_response = parse_image_upload_request(event)

    if error_response:

        return error_response



    try:

        extension = ALLOWED_IMAGE_TYPES[request["contentType"]]

        object_key = (

            f'citizens/{user["sub"]}/complaint-evidence/'

            f'{uuid.uuid4().hex}.{extension}'

        )

        upload_url = create_presigned_upload_url(

            object_key,

            request["contentType"],

        )

        return response(200, {

            "uploadUrl": upload_url,

            "photoKey": object_key,

            "expiresIn": UPLOAD_URL_TTL_SECONDS,

            "requiredHeaders": {"Content-Type": request["contentType"]},

        })

    except Exception as error:

        print(

            "CREATE CITIZEN UPLOAD URL ERROR:",

            repr(error),

            flush=True

        )

        return response(

            500,

            {"error": "Unable to create evidence upload URL"}

        )





def create_officer_proof_upload_url(event, user, complaint_id):

    """Issue a proof-photo URL only to the officer assigned to the complaint."""

    if user["role"] != "Officer":

        return response(403, {"error": "Only officers can upload resolution proof"})

    if not complaint_id or len(complaint_id) > 100:

        return response(400, {"error": "Invalid complaint ID"})



    request, error_response = parse_image_upload_request(event)

    if error_response:

        return error_response



    try:

        complaint = get_table().get_item(

            Key={"complaintId": complaint_id},

            ConsistentRead=True,

        ).get("Item")

        if not complaint:

            return response(404, {"error": "Complaint not found"})

        if complaint.get("assignedOfficerSub") != user["sub"]:

            return response(403, {"error": "Complaint is not assigned to you"})

        if complaint.get("status") != "IN_PROGRESS":

            return response(409, {"error": "Start work before uploading resolution proof"})



        extension = ALLOWED_IMAGE_TYPES[request["contentType"]]

        object_key = (

            f'officers/{user["sub"]}/complaints/{complaint_id}/proof/'

            f'{uuid.uuid4().hex}.{extension}'

        )

        upload_url = create_presigned_upload_url(

            object_key,

            request["contentType"],

        )

        return response(200, {

            "uploadUrl": upload_url,

            "proofPhotoKey": object_key,

            "expiresIn": UPLOAD_URL_TTL_SECONDS,

            "requiredHeaders": {"Content-Type": request["contentType"]},

        })

    except Exception:

        print("CREATE OFFICER PROOF URL ERROR", flush=True)

        return response(500, {"error": "Unable to create proof upload URL"})





def response(status_code, body):



    return {



        "statusCode": status_code,



        "headers": {



            "Content-Type": "application/json",



            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,



            "Access-Control-Allow-Headers": (

                "Content-Type,Authorization,X-Local-Demo-Role,"

                "X-Local-Demo-Sub,X-Local-Demo-Email"

                if LOCAL_DEMO_AUTH_ENABLED

                else "Content-Type,Authorization"

            ),



            "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",



            "Vary": "Origin"



        },



        "body": json.dumps(body, default=str)



    }




# --------------------------------------------------
# EMAIL NOTIFICATIONS AND SLA AUTOMATION
# --------------------------------------------------

SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "").strip()
ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "").strip()
SLA_WARNING_HOURS = max(1, int(os.environ.get("SLA_WARNING_HOURS", "4")))


def send_notification(recipients, subject, body):
    """Send plain-text transactional email through Amazon SES, one recipient at a time."""
    if LOCAL_MODE or not SES_FROM_EMAIL:
        print("EMAIL SKIPPED: SES sender is not configured or local mode is active", flush=True)
        return False

    if isinstance(recipients, str):
        recipients = [recipients]

    cleaned = sorted({
        address.strip()
        for address in recipients
        if isinstance(address, str)
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address.strip())
    })

    if not cleaned:
        print("EMAIL SKIPPED: no valid recipients", flush=True)
        return False

    try:
        ses = boto3.client(
            "ses",
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        )
    except Exception as error:
        print("SES CLIENT ERROR:", repr(error), flush=True)
        return False

    at_least_one_sent = False
    for recipient in cleaned:
        try:
            result = ses.send_email(
                Source=SES_FROM_EMAIL,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject[:200], "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body[:9000], "Charset": "UTF-8"}},
                },
            )
            print(
                "EMAIL SENT TO:", recipient,
                "MESSAGE_ID:", result.get("MessageId"),
                flush=True,
            )
            at_least_one_sent = True
        except Exception as error:
            # In SES sandbox, an unverified recipient may fail while another succeeds.
            print("EMAIL SEND ERROR FOR:", recipient, repr(error), flush=True)

    return at_least_one_sent


def get_cognito_group_emails(group_name):
    """Return verified email attributes for members of a Cognito group."""
    if LOCAL_DEMO_AUTH_ENABLED or not USER_POOL_ID:
        return []

    try:
        cognito = boto3.client(
            "cognito-idp",
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        )
        emails = []
        next_token = None
        while True:
            arguments = {
                "UserPoolId": USER_POOL_ID,
                "GroupName": group_name,
                "Limit": 60,
            }
            if next_token:
                arguments["NextToken"] = next_token

            result = cognito.list_users_in_group(**arguments)
            for member in result.get("Users", []):
                attributes = {
                    item.get("Name"): item.get("Value", "")
                    for item in member.get("Attributes", [])
                }
                email = attributes.get("email", "").strip()
                if email:
                    emails.append(email)

            next_token = result.get("NextToken")
            if not next_token:
                break
        return sorted(set(emails))
    except Exception as error:
        print("COGNITO GROUP EMAIL LOOKUP ERROR:", group_name, repr(error), flush=True)
        return []


def get_officer_email(officer_sub):
    """Resolve the assigned Officer's email by Cognito subject."""
    if not officer_sub or LOCAL_DEMO_AUTH_ENABLED or not USER_POOL_ID:
        return ""

    try:
        cognito = boto3.client(
            "cognito-idp",
            region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        )
        next_token = None
        while True:
            arguments = {
                "UserPoolId": USER_POOL_ID,
                "GroupName": "Officer",
                "Limit": 60,
            }
            if next_token:
                arguments["NextToken"] = next_token
            result = cognito.list_users_in_group(**arguments)

            for member in result.get("Users", []):
                attributes = {
                    item.get("Name"): item.get("Value", "")
                    for item in member.get("Attributes", [])
                }
                if attributes.get("sub") == officer_sub:
                    return attributes.get("email", "").strip()

            next_token = result.get("NextToken")
            if not next_token:
                return ""
    except Exception as error:
        print("OFFICER EMAIL LOOKUP ERROR:", repr(error), flush=True)
        return ""


def admin_recipients():
    """Use the configured admin mailbox, falling back to Cognito Admin users."""
    if ADMIN_NOTIFICATION_EMAIL:
        return [ADMIN_NOTIFICATION_EMAIL]
    return get_cognito_group_emails("Admin")


def complaint_email_body(complaint, message, include_sla=True):
    lines = [
        message,
        "",
        f"Complaint ID: {complaint.get('complaintId', 'N/A')}",
        f"Title: {complaint.get('title', 'N/A')}",
        f"Category: {complaint.get('category', 'N/A')}",
        f"Location: {complaint.get('location', 'N/A')}",
        f"Status: {complaint.get('status', 'N/A')}",
    ]
    if complaint.get("assignedOfficerName"):
        lines.append(f"Assigned officer: {complaint['assignedOfficerName']}")
    if include_sla and complaint.get("createdAt"):
        lines.append(f"SLA: {complaint.get('slaHours', 48)} hours from submission")
    lines.extend(["", "This is an automated notification from CivicTrack."])
    return "\n".join(lines)


def notify_complaint_created(complaint):
    body = complaint_email_body(
        complaint,
        "Your CivicTrack complaint has been registered successfully.",
    )
    recipients = [complaint.get("citizenEmail", "")]
    send_notification(
        recipients,
        f"CivicTrack complaint registered: {complaint['complaintId']}",
        body,
    )
    send_notification(
        admin_recipients(),
        f"New CivicTrack complaint: {complaint['complaintId']}",
        complaint_email_body(complaint, "A new citizen complaint has been submitted."),
    )


def notify_complaint_assigned(complaint):
    body = complaint_email_body(
        complaint,
        "Your complaint has been assigned to an officer.",
    )
    send_notification(
        [complaint.get("citizenEmail", "")],
        f"CivicTrack update: {complaint['complaintId']} assigned",
        body,
    )
    officer_email = get_officer_email(complaint.get("assignedOfficerSub", ""))
    send_notification(
        [officer_email],
        f"New complaint assigned to you: {complaint['complaintId']}",
        complaint_email_body(complaint, "A complaint has been assigned to you. Please review it in the CivicTrack Officer Dashboard."),
    )


def notify_work_started(complaint):
    send_notification(
        [complaint.get("citizenEmail", "")],
        f"CivicTrack update: work started on {complaint['complaintId']}",
        complaint_email_body(complaint, "An officer has started working on your complaint."),
    )


def notify_complaint_resolved(complaint):
    send_notification(
        [complaint.get("citizenEmail", "")],
        f"CivicTrack complaint resolved: {complaint['complaintId']}",
        complaint_email_body(
            complaint,
            "Your complaint has been marked as resolved. Resolution proof was uploaded by the assigned officer.",
        ),
    )
    send_notification(
        admin_recipients(),
        f"CivicTrack complaint resolved: {complaint['complaintId']}",
        complaint_email_body(complaint, "An officer has resolved this complaint."),
    )


def parse_iso_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def process_sla_checks():
    """
    Scheduled EventBridge job:
    - Sends one warning when a complaint enters the final SLA_WARNING_HOURS.
    - Escalates overdue unresolved complaints once.
    - Retries a failed escalation email without repeating a successful one.
    """
    table = get_table()
    now = datetime.now(timezone.utc)
    scan_args = {
        "FilterExpression": Attr("status").ne("RESOLVED") & Attr("createdAt").exists(),
        "ProjectionExpression": (
            "complaintId, #status, title, category, #loc, description, "
            "citizenEmail, assignedOfficerSub, assignedOfficerName, "
            "createdAt, slaHours, escalated, slaWarningSentAt, "
            "escalationNotifiedAt"
        ),
        "ExpressionAttributeNames": {
            "#status": "status",
            "#loc": "location",
        },
    }

    scanned = 0
    warnings_sent = 0
    escalations = 0
    emails_sent = 0
    last_key = None

    while True:
        if last_key:
            scan_args["ExclusiveStartKey"] = last_key
        result = table.scan(**scan_args)
        complaints = result.get("Items", [])
        scanned += len(complaints)

        for complaint in complaints:
            created_at = parse_iso_datetime(complaint.get("createdAt"))
            if not created_at:
                continue

            try:
                sla_hours = max(1, int(complaint.get("slaHours", 48)))
            except (TypeError, ValueError):
                sla_hours = 48

            deadline = created_at + timedelta(hours=sla_hours)
            warning_at = deadline - timedelta(hours=SLA_WARNING_HOURS)
            complaint_id = complaint.get("complaintId")
            if not complaint_id:
                continue

            officer_email = get_officer_email(complaint.get("assignedOfficerSub", ""))
            recipients = [officer_email] + admin_recipients()

            if now >= deadline:
                if not complaint.get("escalated", False):
                    try:
                        table.update_item(
                            Key={"complaintId": complaint_id},
                            UpdateExpression="SET escalated = :yes, escalatedAt = :now",
                            ConditionExpression=(
                                "attribute_exists(complaintId) AND "
                                "#status <> :resolved AND "
                                "(attribute_not_exists(escalated) OR escalated = :no)"
                            ),
                            ExpressionAttributeNames={"#status": "status"},
                            ExpressionAttributeValues={
                                ":yes": True,
                                ":no": False,
                                ":resolved": "RESOLVED",
                                ":now": now.isoformat(),
                            },
                        )
                        complaint["escalated"] = True
                        escalations += 1
                    except ClientError as error:
                        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                            print("SLA ESCALATION UPDATE ERROR:", repr(error), flush=True)
                            continue

                if complaint.get("escalationNotifiedAt") is None:
                    sent = send_notification(
                        recipients,
                        f"CivicTrack SLA BREACHED: {complaint_id}",
                        complaint_email_body(
                            complaint,
                            f"The {sla_hours}-hour SLA has been breached. This complaint has been escalated and requires immediate attention.",
                        ),
                    )
                    if sent:
                        table.update_item(
                            Key={"complaintId": complaint_id},
                            UpdateExpression="SET escalationNotifiedAt = :now",
                            ConditionExpression="attribute_not_exists(escalationNotifiedAt)",
                            ExpressionAttributeValues={":now": now.isoformat()},
                        )
                        emails_sent += 1

            elif now >= warning_at and not complaint.get("slaWarningSentAt"):
                sent = send_notification(
                    recipients,
                    f"CivicTrack SLA warning: {complaint_id}",
                    complaint_email_body(
                        complaint,
                        f"This complaint is approaching its {sla_hours}-hour SLA deadline. Please take action before {deadline.isoformat()}.",
                    ),
                )
                if sent:
                    table.update_item(
                        Key={"complaintId": complaint_id},
                        UpdateExpression="SET slaWarningSentAt = :now",
                        ConditionExpression="attribute_not_exists(slaWarningSentAt)",
                        ExpressionAttributeValues={":now": now.isoformat()},
                    )
                    warnings_sent += 1
                    emails_sent += 1

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

    summary = {
        "status": "completed",
        "scanned": scanned,
        "warningsSent": warnings_sent,
        "newEscalations": escalations,
        "notificationEmailsSent": emails_sent,
    }
    print("SLA CHECK SUMMARY:", json.dumps(summary), flush=True)
    return summary




# --------------------------------------------------



# AUTHENTICATION CONTEXT



# --------------------------------------------------



def get_authorizer_claims(event):



    """



    Read claims supplied by API Gateway after JWT validation.



    Supports:



    - API Gateway REST API Cognito authorizer



    - API Gateway HTTP API JWT authorizer



    """



    request_context = event.get("requestContext") or {}



    authorizer = request_context.get("authorizer") or {}



    claims = authorizer.get("claims")



    if isinstance(claims, dict):



        return claims



    jwt_context = authorizer.get("jwt") or {}



    claims = jwt_context.get("claims")



    if isinstance(claims, dict):



        return claims



    return None



def get_user_groups(claims):



    """Normalize the Cognito groups claim."""



    groups = claims.get("cognito:groups", [])



    if isinstance(groups, list):



        return groups



    if isinstance(groups, str):



        try:



            parsed = json.loads(groups)



            if isinstance(parsed, list):



                return parsed



        except json.JSONDecodeError:



            pass



        return [



            group.strip()



            for group in groups.split(",")



            if group.strip()



        ]



    return []



def authenticate_request(event):



    """Identify a verified Cognito user, or an explicitly enabled SAM demo user."""



    # This branch is intentionally unavailable outside SAM local. It exists only

    # to let the local frontend exercise Citizen/Officer/Admin workflows without

    # creating Cognito resources or deploying synthetic authentication.

    if LOCAL_DEMO_AUTH_ENABLED:

        headers = event.get("headers") or {}

        normalized_headers = {str(key).lower(): value for key, value in headers.items()}

        demo_role = normalized_headers.get("x-local-demo-role", "")

        demo_sub = normalized_headers.get("x-local-demo-sub", "")

        demo_email = normalized_headers.get("x-local-demo-email", "")



        if demo_role not in VALID_ROLES or not isinstance(demo_sub, str) or not demo_sub.strip():

            return None, response(401, {"error": "Valid local demo identity required"})



        return {

            "sub": demo_sub.strip()[:128],

            "email": demo_email.strip()[:254] if isinstance(demo_email, str) else "",

            "role": demo_role,

        }, None



    claims = get_authorizer_claims(event)



    if not claims:



        return None, response(401, {



            "error": "Authentication required"



        })



    user_sub = claims.get("sub")



    if not user_sub:



        return None, response(401, {



            "error": "Invalid authentication identity"



        })



    groups = get_user_groups(claims)



    if "Admin" in groups:



        role = "Admin"



    elif "Officer" in groups:



        role = "Officer"



    elif "Citizen" in groups:



        role = "Citizen"



    else:



        return None, response(403, {



            "error": "Your account does not have a CivicTrack role"



        })



    return {



        "sub": user_sub,



        "email": claims.get("email", ""),



        "role": role



    }, None



# --------------------------------------------------



# ROUTE HELPERS



# --------------------------------------------------



def get_assignment_complaint_id(event, path):



    """Return an ID only for the /assign route, never /start or /resolve."""



    match = re.fullmatch(r"/complaints/([^/]+)/assign", path.rstrip("/"))



    if not match:



        return None



    path_parameters = event.get("pathParameters") or {}



    return path_parameters.get("complaintId") or unquote(match.group(1))



# --------------------------------------------------



# REQUEST ROUTER



# --------------------------------------------------



def lambda_handler(event, context):

    # EventBridge scheduled SLA sweep (not an API Gateway request).
    if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
        return process_sla_checks()




    method = event.get("httpMethod") or (



        event.get("requestContext", {})



        .get("http", {})



        .get("method", "")



    )



    path = event.get("path") or event.get("rawPath", "")



    normalized_path = path.rstrip("/") or "/"



    if method == "OPTIONS":



        return response(200, {"message": "CORS OK"})



    # Public health endpoint.



    if normalized_path == "/health" and method == "GET":



        return response(200, {



            "status": "healthy",



            "application": "CivicTrack API"



        })



    # Citizen evidence upload URL.

    if normalized_path == "/evidence/upload-url" and method == "POST":

        user, auth_error = authenticate_request(event)

        if auth_error:

            return auth_error

        return create_citizen_upload_url(event, user)



    # Officer resolution-proof upload URL.

    proof_upload_match = re.fullmatch(

        r"/complaints/([^/]+)/proof-upload-url",

        normalized_path,

    )

    if proof_upload_match:

        user, auth_error = authenticate_request(event)

        if auth_error:

            return auth_error

        if method != "POST":

            return response(405, {"error": "Method not allowed"})

        complaint_id = unquote(proof_upload_match.group(1))

        return create_officer_proof_upload_url(event, user, complaint_id)



    # Complaint collection routes.



    if normalized_path == "/complaints":



        user, auth_error = authenticate_request(event)



        if auth_error:



            return auth_error



        if method == "POST":



            return create_complaint(event, user)



        if method == "GET":



            return get_complaints(user)



        return response(405, {



            "error": "Method not allowed"



        })



    # Officer workflow routes.



    workflow_match = re.fullmatch(



        r"/complaints/([^/]+)/(start|resolve)", normalized_path



    )



    if workflow_match:



        user, auth_error = authenticate_request(event)



        if auth_error:



            return auth_error



        if method != "PATCH":



            return response(405, {"error": "Method not allowed"})



        complaint_id = unquote(workflow_match.group(1))



        action = workflow_match.group(2)



        return update_officer_workflow(event, user, complaint_id, action)



    # Admin complaint assignment route.



    complaint_id = get_assignment_complaint_id(



        event,



        normalized_path



    )



    if complaint_id is not None:



        user, auth_error = authenticate_request(event)



        if auth_error:



            return auth_error



        if method == "PATCH":



            return assign_complaint(



                event,



                user,



                complaint_id



            )



        return response(405, {



            "error": "Method not allowed"



        })



    return response(404, {



        "error": "Route not found"



    })



# --------------------------------------------------



# CREATE COMPLAINT



# --------------------------------------------------



def create_complaint(event, user):



    if user["role"] != "Citizen":



        return response(403, {



            "error": "Only citizens can register complaints"



        })



    try:



        body = json.loads(event.get("body") or "{}")



        if not isinstance(body, dict):



            return response(400, {



                "error": "Invalid request body"



            })



        title = body.get("title")



        category = body.get("category")



        location = body.get("location")



        description = body.get("description")



        fields = {



            "title": title,



            "category": category,



            "location": location,



            "description": description



        }



        for field_name, value in fields.items():



            if not isinstance(value, str) or not value.strip():



                return response(400, {



                    "error": f"{field_name} is required"



                })



        title = title.strip()



        category = category.strip()



        location = location.strip()



        description = description.strip()



        if len(title) > 150:



            return response(400, {



                "error": "Title must be 150 characters or fewer"



            })



        if len(category) > 60:



            return response(400, {



                "error": "Category is too long"



            })



        if len(location) > 250:



            return response(400, {



                "error": "Location is too long"



            })



        if len(description) > 3000:



            return response(400, {



                "error": "Description must be 3000 characters or fewer"



            })



        photo_key = body.get("photoKey")

        if photo_key is not None:

            expected_prefix = f'citizens/{user["sub"]}/complaint-evidence/'

            valid_photo, photo_error = validate_uploaded_image(

                photo_key,

                expected_prefix,

            )

            if not valid_photo:

                return response(400, {"error": photo_error})



        now = datetime.now(timezone.utc).isoformat()



        complaint = {



            "complaintId": f"CT-{uuid.uuid4().hex[:10].upper()}",



            "title": title,



            "category": category,



            "location": location,



            "description": description,



            "photoKey": photo_key or "",



            # Identity comes from verified JWT claims.



            "citizenSub": user["sub"],



            "citizenEmail": user["email"],



            "assignedOfficerSub": "",



            "assignedOfficerName": "",



            "status": "PENDING",



            "priority": "MEDIUM",



            "createdAt": now,



            "slaHours": 48,



            "escalated": False



        }



        print(



            "POST: Saving complaint:",



            complaint["complaintId"],



            flush=True



        )



        table = get_table()



        table.put_item(Item=complaint)

        # Email failures never roll back a successfully stored complaint.
        notify_complaint_created(complaint)

        print("POST: Complaint saved successfully", flush=True)



        return response(201, {



            "message": "Complaint registered successfully",



            "complaint": complaint



        })



    except json.JSONDecodeError:



        return response(400, {



            "error": "Invalid JSON"



        })



    except Exception:



        print("CREATE COMPLAINT ERROR", flush=True)



        return response(500, {



            "error": "Unable to create complaint"



        })



# --------------------------------------------------



# LIST COMPLAINTS



# --------------------------------------------------



def get_complaints(user):



    try:



        table = get_table()



        scan_arguments = {



            "ConsistentRead": True



        }



        if user["role"] == "Citizen":



            scan_arguments["FilterExpression"] = Attr(



                "citizenSub"



            ).eq(user["sub"])



        elif user["role"] == "Officer":



            scan_arguments["FilterExpression"] = Attr(



                "assignedOfficerSub"



            ).eq(user["sub"])



        elif user["role"] != "Admin":



            return response(403, {



                "error": "Insufficient permissions"



            })



        items = []



        last_evaluated_key = None



        while True:



            if last_evaluated_key:



                scan_arguments["ExclusiveStartKey"] = last_evaluated_key



            result = table.scan(**scan_arguments)



            items.extend(result.get("Items", []))



            last_evaluated_key = result.get("LastEvaluatedKey")



            if not last_evaluated_key:



                break



        print(



            "GET: Retrieved complaints for role:",



            user["role"],



            "COUNT:",



            len(items),



            flush=True



        )



        return response(200, {



            "count": len(items),



            "complaints": items



        })



    except Exception:



        print("GET COMPLAINTS ERROR", flush=True)



        return response(500, {



            "error": "Unable to retrieve complaints"



        })



# --------------------------------------------------



# ASSIGN COMPLAINT TO OFFICER



# --------------------------------------------------





def get_verified_officer_name(officer_sub):
    """Return the Cognito name only if the subject belongs to the Officer group."""
    if LOCAL_DEMO_AUTH_ENABLED:
        return None

    if not USER_POOL_ID:
        raise RuntimeError("USER_POOL_ID is not configured")

    cognito = boto3.client(
        "cognito-idp",
        region_name=os.environ.get("AWS_REGION", "ap-south-1"),
    )
    next_token = None

    while True:
        arguments = {
            "UserPoolId": USER_POOL_ID,
            "GroupName": "Officer",
            "Limit": 60,
        }
        if next_token:
            arguments["NextToken"] = next_token

        result = cognito.list_users_in_group(**arguments)

        for member in result.get("Users", []):
            attributes = {
                item.get("Name"): item.get("Value", "")
                for item in member.get("Attributes", [])
            }
            if attributes.get("sub") == officer_sub:
                return attributes.get("name") or member.get("Username") or "Officer"

        next_token = result.get("NextToken")
        if not next_token:
            return None


def assign_complaint(event, user, complaint_id):



    # Only Admins can assign complaints.



    if user["role"] != "Admin":



        return response(403, {



            "error": "Only Admins can assign complaints"



        })



    if not complaint_id or len(complaint_id) > 100:



        return response(400, {



            "error": "Invalid complaint ID"



        })



    try:



        body = json.loads(event.get("body") or "{}")



        if not isinstance(body, dict):



            return response(400, {



                "error": "Invalid request body"



            })



        officer_sub = body.get("officerSub")



        officer_name = body.get("officerName")



        if not isinstance(officer_sub, str) or not officer_sub.strip():



            return response(400, {



                "error": "Officer ID is required"



            })



        if not isinstance(officer_name, str) or not officer_name.strip():



            return response(400, {



                "error": "Officer name is required"



            })



        officer_sub = officer_sub.strip()



        officer_name = officer_name.strip()



        if len(officer_sub) > 128 or len(officer_name) > 150:



            return response(400, {



                "error": "Officer details exceed allowed length"



            })

        # Production assignments must target a real member of the Cognito Officer group.
        # Local demo identities remain available only in SAM local mode.
        if not LOCAL_DEMO_AUTH_ENABLED:
            verified_name = get_verified_officer_name(officer_sub)
            if not verified_name:
                return response(400, {
                    "error": "Selected user is not a member of the CivicTrack Officer group"
                })
            officer_name = verified_name



        table = get_table()



        # Confirm the complaint exists and is not already assigned.



        existing = table.get_item(



            Key={"complaintId": complaint_id},



            ConsistentRead=True



        )



        complaint = existing.get("Item")



        if not complaint:



            return response(404, {



                "error": "Complaint not found"



            })



        if complaint.get("assignedOfficerSub", ""):



            return response(409, {



                "error": "Complaint is already assigned to an officer"



            })



        now = datetime.now(timezone.utc).isoformat()



        # ConditionExpression protects against simultaneous assignments.



        result = table.update_item(



            Key={"complaintId": complaint_id},



            UpdateExpression=(



                "SET assignedOfficerSub = :officerSub, "



                "assignedOfficerName = :officerName, "



                "assignedAt = :assignedAt, "



                "assignedBySub = :adminSub"



            ),



            ConditionExpression=(



                "attribute_exists(complaintId) AND "



                "(attribute_not_exists(assignedOfficerSub) "



                "OR assignedOfficerSub = :empty)"



            ),



            ExpressionAttributeValues={



                ":officerSub": officer_sub,



                ":officerName": officer_name,



                ":assignedAt": now,



                ":adminSub": user["sub"],



                ":empty": ""



            },



            ReturnValues="ALL_NEW"



        )



        updated_complaint = result.get("Attributes", {})

        # Notify the citizen and the newly assigned officer.
        notify_complaint_assigned(updated_complaint)



        print(



            "ASSIGN: Complaint",



            complaint_id,



            "assigned to",



            officer_sub,



            flush=True



        )



        return response(200, {



            "message": "Complaint assigned successfully",



            "complaint": updated_complaint



        })



    except json.JSONDecodeError:



        return response(400, {



            "error": "Invalid JSON"



        })



    except ClientError as error:



        error_code = error.response.get(



            "Error", {}



        ).get("Code")



        if error_code == "ConditionalCheckFailedException":



            return response(409, {



                "error": "Complaint was assigned by another request or is no longer available"



            })



        print("ASSIGN COMPLAINT ERROR:", error_code, flush=True)



        return response(500, {



            "error": "Unable to assign complaint"



        })



    except Exception:



        print("ASSIGN COMPLAINT ERROR", flush=True)



        return response(500, {



            "error": "Unable to assign complaint"



        })



# --------------------------------------------------



# OFFICER WORKFLOW



# --------------------------------------------------



def update_officer_workflow(event, user, complaint_id, action):



    """Assigned officers alone can start or resolve their own complaints."""



    if user["role"] != "Officer":



        return response(403, {"error": "Only officers can update complaint progress"})



    if not complaint_id or len(complaint_id) > 100:



        return response(400, {"error": "Invalid complaint ID"})



    if action not in ("start", "resolve"):



        return response(404, {"error": "Unknown workflow action"})



    proof_key = None



    if action == "resolve":



        try:



            body = json.loads(event.get("body") or "{}")



        except json.JSONDecodeError:



            return response(400, {"error": "Invalid JSON"})



        if not isinstance(body, dict):



            return response(400, {"error": "Invalid request body"})



        proof_key = body.get("proofPhotoKey")



        if (not isinstance(proof_key, str) or not proof_key.strip()



                or len(proof_key) > 512):



            return response(400, {"error": "A valid proofPhotoKey is required"})



        proof_key = proof_key.strip()



        # Only object keys, not arbitrary external URLs.



        if (proof_key.startswith(("/", "\\\\\\\\")) or ".." in proof_key.split("/")



                or "://" in proof_key or any(ord(ch) < 32 for ch in proof_key)):



            return response(400, {"error": "Invalid proofPhotoKey"})



    try:



        table = get_table()



        existing = table.get_item(



            Key={"complaintId": complaint_id}, ConsistentRead=True



        ).get("Item")



        if not existing:



            return response(404, {"error": "Complaint not found"})



        if existing.get("assignedOfficerSub") != user["sub"]:



            return response(403, {"error": "Complaint is not assigned to you"})



        expected_status = "PENDING" if action == "start" else "IN_PROGRESS"



        new_status = "IN_PROGRESS" if action == "start" else "RESOLVED"



        if existing.get("status") != expected_status:



            return response(409, {



                "error": f"Complaint must be {expected_status} before this action"



            })



        if action == "resolve":

            expected_prefix = (

                f'officers/{user["sub"]}/complaints/{complaint_id}/proof/'

            )

            valid_proof, proof_error = validate_uploaded_image(

                proof_key,

                expected_prefix,

            )

            if not valid_proof:

                return response(400, {"error": proof_error})



        now = datetime.now(timezone.utc).isoformat()



        values = {



            ":officer": user["sub"],



            ":expected": expected_status,



            ":status": new_status,



            ":now": now,



        }



        if action == "start":



            expression = "SET #status = :status, workStartedAt = :now"



        else:



            expression = (



                "SET #status = :status, resolvedAt = :now, "



                "resolvedBySub = :officer, proofPhotoKey = :proof"



            )



            values[":proof"] = proof_key



        result = table.update_item(



            Key={"complaintId": complaint_id},



            UpdateExpression=expression,



            ConditionExpression=(



                "attribute_exists(complaintId) AND "



                "assignedOfficerSub = :officer AND #status = :expected"



            ),



            ExpressionAttributeNames={"#status": "status"},



            ExpressionAttributeValues=values,



            ReturnValues="ALL_NEW",



        )



        updated_complaint = result.get("Attributes", {})

        if action == "start":
            notify_work_started(updated_complaint)
        else:
            notify_complaint_resolved(updated_complaint)

        return response(200, {
            "message": "Work started successfully" if action == "start"
                       else "Complaint resolved successfully",
            "complaint": updated_complaint,
        })



    except ClientError as error:



        code = error.response.get("Error", {}).get("Code")



        if code == "ConditionalCheckFailedException":



            return response(409, {



                "error": "Complaint assignment or status changed; refresh and retry"



            })



        print("OFFICER WORKFLOW ERROR:", code, flush=True)



        return response(500, {"error": "Unable to update complaint"})



    except Exception:



        print("OFFICER WORKFLOW ERROR", flush=True)



        return response(500, {"error": "Unable to update complaint"})
