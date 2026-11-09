"""CivicTrack complaints API and SLA automation (AWS Lambda, Python 3.14).

Entry point: ``lambda_handler``.
  * API Gateway (REST, Cognito authorizer) requests are routed by path/method.
  * EventBridge scheduled events run the SLA sweep (``process_sla_checks``).

Authorization is enforced here, never trusted to the frontend:
  Citizen -> only complaints they created
  Officer -> only complaints assigned to them
  Admin   -> all complaints and all administrative actions
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError

import lifecycle as lc

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

TABLE_NAME = os.environ.get("COMPLAINTS_TABLE", "CivicTrack-Complaints")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
LOCAL_MODE = os.environ.get("AWS_SAM_LOCAL") == "true"

# Synthetic identities are accepted only by SAM local when explicitly enabled.
# Never set ENABLE_LOCAL_DEMO_AUTH in a deployed environment.
LOCAL_DEMO_AUTH_ENABLED = LOCAL_MODE and os.environ.get("ENABLE_LOCAL_DEMO_AUTH") == "true"

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:5173")

S3_BUCKET = os.environ.get("S3_BUCKET", "civictrack-evidence")
S3_INTERNAL_ENDPOINT_URL = os.environ.get(
    "S3_INTERNAL_ENDPOINT_URL", "http://host.docker.internal:8333" if LOCAL_MODE else None
) or None
S3_PRESIGN_ENDPOINT_URL = os.environ.get(
    "S3_PRESIGN_ENDPOINT_URL", "http://localhost:8333" if LOCAL_MODE else None
) or None
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")
UPLOAD_URL_TTL_SECONDS = int(os.environ.get("UPLOAD_URL_TTL_SECONDS", "300"))
VIEW_URL_TTL_SECONDS = int(os.environ.get("VIEW_URL_TTL_SECONDS", "300"))
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "").strip()
ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "").strip()
SLA_WARNING_HOURS = max(1, int(os.environ.get("SLA_WARNING_HOURS", "4")))

VALID_ROLES = {"Citizen", "Officer", "Admin"}
COMPLAINT_ID_PATTERN = re.compile(r"CT-[A-Z0-9]{6,32}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MAX_TEXT = {"title": 150, "category": 60, "location": 250, "description": 3000}
MAX_NOTE = 1000
MAX_AREA = 80
MAX_HISTORY = 200

REGION = os.environ.get("AWS_REGION", "ap-south-1")


def utcnow():
    return datetime.now(timezone.utc)


def log(*parts):
    print(*parts, flush=True)


# --------------------------------------------------
# HTTP HELPERS
# --------------------------------------------------

def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": (
                "Content-Type,Authorization,X-Local-Demo-Role,X-Local-Demo-Sub,X-Local-Demo-Email"
                if LOCAL_DEMO_AUTH_ENABLED
                else "Content-Type,Authorization"
            ),
            "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
            "Vary": "Origin",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=_json_default),
    }


class RequestError(Exception):
    """Raised to short-circuit a handler with an HTTP error response."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def parse_body(event, required=True):
    raw = event.get("body")
    if raw in (None, "") and not required:
        return {}
    try:
        body = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        raise RequestError(400, "Invalid JSON")
    if not isinstance(body, dict):
        raise RequestError(400, "Invalid request body")
    return body


def optional_text(body, field, max_length):
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestError(400, f"{field} must be text")
    value = value.strip()
    if len(value) > max_length:
        raise RequestError(400, f"{field} must be {max_length} characters or fewer")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise RequestError(400, f"{field} contains invalid characters")
    return value


def validate_complaint_id(complaint_id):
    if not isinstance(complaint_id, str) or not COMPLAINT_ID_PATTERN.fullmatch(complaint_id):
        raise RequestError(400, "Invalid complaint ID")
    return complaint_id


# --------------------------------------------------
# AWS CLIENTS
# --------------------------------------------------

def get_table():
    if LOCAL_MODE:
        dynamodb = boto3.resource(
            "dynamodb",
            endpoint_url="http://host.docker.internal:8000",
            region_name="ap-south-1",
            aws_access_key_id="dummy",
            aws_secret_access_key="dummy",
        )
    else:
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
    return dynamodb.Table(TABLE_NAME)


def get_s3_client(for_presign=False):
    """Lambda in Docker reaches SeaweedFS via host.docker.internal; the browser
    receives presigned URLs for localhost. In AWS, the execution role is used."""
    endpoint_url = S3_PRESIGN_ENDPOINT_URL if for_presign else S3_INTERNAL_ENDPOINT_URL
    client_args = {
        "region_name": REGION,
        "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    }
    if endpoint_url:
        client_args["endpoint_url"] = endpoint_url
    if LOCAL_MODE:
        if not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:
            raise RuntimeError("Local S3 credentials are missing (S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY).")
        client_args["aws_access_key_id"] = S3_ACCESS_KEY_ID
        client_args["aws_secret_access_key"] = S3_SECRET_ACCESS_KEY
    return boto3.client("s3", **client_args)


def get_cognito_client():
    return boto3.client("cognito-idp", region_name=REGION)


# --------------------------------------------------
# OBJECT STORAGE
# --------------------------------------------------

def validate_object_key(value):
    """Reject URLs, absolute paths, traversal and control characters."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or len(value) > 512:
        return False
    if value.startswith(("/", "\\")) or ".." in value.split("/"):
        return False
    if "://" in value or any(ord(char) < 32 for char in value):
        return False
    return True


def validate_uploaded_image(object_key, expected_prefix):
    """Confirm an uploaded image exists and meets the evidence constraints."""
    if not validate_object_key(object_key) or not object_key.startswith(expected_prefix):
        return False, "Invalid evidence object key"
    try:
        metadata = get_s3_client().head_object(Bucket=S3_BUCKET, Key=object_key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False, "Evidence photo has not been uploaded"
        log("S3 HEAD ERROR:", code)
        raise
    if metadata.get("ContentType", "") not in ALLOWED_IMAGE_TYPES:
        return False, "Evidence must be a JPEG, PNG or WebP image"
    length = metadata.get("ContentLength", 0)
    if length <= 0 or length > MAX_EVIDENCE_BYTES:
        return False, "Evidence image must be between 1 byte and 10 MB"
    return True, None


def create_presigned_upload_url(object_key, content_type):
    return get_s3_client(for_presign=True).generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET, "Key": object_key, "ContentType": content_type},
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        HttpMethod="PUT",
    )


def create_presigned_view_url(object_key):
    """Short-lived read URL. Presigning is a local computation (no S3 call)."""
    if not validate_object_key(object_key):
        return None
    try:
        return get_s3_client(for_presign=True).generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": object_key},
            ExpiresIn=VIEW_URL_TTL_SECONDS,
        )
    except Exception as error:  # never fail a read because of a preview URL
        log("PRESIGN VIEW ERROR:", type(error).__name__)
        return None


def parse_image_upload_request(event):
    body = parse_body(event)
    content_type = body.get("contentType")
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise RequestError(400, "contentType must be image/jpeg, image/png or image/webp")
    file_size = body.get("fileSize")
    if file_size is not None:
        if not isinstance(file_size, int) or isinstance(file_size, bool) or file_size <= 0:
            raise RequestError(400, "fileSize must be a positive integer")
        if file_size > MAX_EVIDENCE_BYTES:
            raise RequestError(400, "Evidence image must be 10 MB or smaller")
    return body, content_type


def officer_media_prefix(officer_sub, complaint_id, stage):
    folder = "before" if stage == "before" else "proof"
    return f"officers/{officer_sub}/complaints/{complaint_id}/{folder}/"


def create_citizen_upload_url(event, user):
    if user["role"] != "Citizen":
        raise RequestError(403, "Only citizens can upload complaint photos")
    _, content_type = parse_image_upload_request(event)
    key = f'citizens/{user["sub"]}/complaint-evidence/{uuid.uuid4().hex}.{ALLOWED_IMAGE_TYPES[content_type]}'
    return response(200, {
        "uploadUrl": create_presigned_upload_url(key, content_type),
        "photoKey": key,
        "expiresIn": UPLOAD_URL_TTL_SECONDS,
        "requiredHeaders": {"Content-Type": content_type},
    })


def create_officer_proof_upload_url(event, user, complaint_id):
    """Issue a before/after photo URL only to the officer assigned to the complaint."""
    if user["role"] != "Officer":
        raise RequestError(403, "Only officers can upload resolution proof")
    validate_complaint_id(complaint_id)
    body, content_type = parse_image_upload_request(event)
    stage = body.get("stage", "after")
    if stage not in ("before", "after"):
        raise RequestError(400, "stage must be 'before' or 'after'")

    complaint = load_complaint(complaint_id)
    require_complaint_access(complaint, user)
    status = lc.effective_status(complaint)
    if stage == "after" and status != lc.IN_PROGRESS:
        raise RequestError(409, "Start work before uploading resolution proof")
    if stage == "before" and status not in (lc.ASSIGNED, lc.REOPENED, lc.IN_PROGRESS):
        raise RequestError(409, "Before photos can only be added before the complaint is resolved")

    key = officer_media_prefix(user["sub"], complaint_id, stage) + f"{uuid.uuid4().hex}.{ALLOWED_IMAGE_TYPES[content_type]}"
    return response(200, {
        "uploadUrl": create_presigned_upload_url(key, content_type),
        "proofPhotoKey": key,
        "photoKey": key,
        "stage": stage,
        "expiresIn": UPLOAD_URL_TTL_SECONDS,
        "requiredHeaders": {"Content-Type": content_type},
    })


# --------------------------------------------------
# COGNITO DIRECTORY
# --------------------------------------------------

def list_group_users(group_name):
    """Return [{sub, email, name, username, enabled, status}] for a Cognito group."""
    if LOCAL_DEMO_AUTH_ENABLED or not USER_POOL_ID:
        return []
    cognito = get_cognito_client()
    users, next_token = [], None
    while True:
        arguments = {"UserPoolId": USER_POOL_ID, "GroupName": group_name, "Limit": 60}
        if next_token:
            arguments["NextToken"] = next_token
        result = cognito.list_users_in_group(**arguments)
        for member in result.get("Users", []):
            attributes = {a.get("Name"): a.get("Value", "") for a in member.get("Attributes", [])}
            users.append({
                "sub": attributes.get("sub", ""),
                "email": attributes.get("email", "").strip(),
                "name": attributes.get("name") or member.get("Username") or "",
                "username": member.get("Username", ""),
                "enabled": bool(member.get("Enabled", True)),
                "status": member.get("UserStatus", ""),
            })
        next_token = result.get("NextToken")
        if not next_token:
            return users


def get_cognito_group_emails(group_name):
    try:
        return sorted({u["email"] for u in list_group_users(group_name) if u["email"]})
    except Exception as error:
        log("COGNITO GROUP EMAIL LOOKUP ERROR:", group_name, type(error).__name__)
        return []


def find_officer(officer_sub):
    """Return the Officer group member with this sub, or None."""
    if not officer_sub:
        return None
    for officer in list_group_users("Officer"):
        if officer["sub"] == officer_sub:
            return officer
    return None


def get_officer_email(officer_sub, cache=None):
    if not officer_sub or LOCAL_DEMO_AUTH_ENABLED or not USER_POOL_ID:
        return ""
    if cache is not None and officer_sub in cache:
        return cache[officer_sub]
    try:
        officer = find_officer(officer_sub)
        email = officer["email"] if officer else ""
    except Exception as error:
        log("OFFICER EMAIL LOOKUP ERROR:", type(error).__name__)
        email = ""
    if cache is not None:
        cache[officer_sub] = email
    return email


DEMO_OFFICERS = [
    {"sub": "local-demo-officer", "email": "demo.officer@civictrack.local", "name": "Demo Officer", "username": "demo.officer", "enabled": True, "status": "CONFIRMED"},
    {"sub": "local-demo-officer-2", "email": "demo.officer2@civictrack.local", "name": "Demo Officer Two", "username": "demo.officer2", "enabled": True, "status": "CONFIRMED"},
]


def list_officers(user):
    if user["role"] != "Admin":
        raise RequestError(403, "Only Admins can view the officer directory")
    if LOCAL_DEMO_AUTH_ENABLED:
        return response(200, {"officers": DEMO_OFFICERS, "source": "local-demo"})
    if not USER_POOL_ID:
        raise RequestError(500, "Officer directory is not configured")
    officers = [
        {k: o[k] for k in ("sub", "email", "name", "enabled", "status")}
        for o in list_group_users("Officer") if o["sub"]
    ]
    officers.sort(key=lambda o: (o["name"] or o["email"]).lower())
    return response(200, {"officers": officers, "source": "cognito"})


# --------------------------------------------------
# EMAIL NOTIFICATIONS
# --------------------------------------------------

def send_notification(recipients, subject, body):
    """Send plain-text email through SES, one recipient at a time.
    Returns True if at least one message was accepted by SES."""
    if LOCAL_MODE or not SES_FROM_EMAIL:
        log("EMAIL SKIPPED: SES sender not configured or local mode")
        return False
    if isinstance(recipients, str):
        recipients = [recipients]
    cleaned = sorted({
        address.strip() for address in recipients or []
        if isinstance(address, str) and EMAIL_PATTERN.fullmatch(address.strip())
    })
    if not cleaned:
        log("EMAIL SKIPPED: no valid recipients")
        return False
    try:
        ses = boto3.client("ses", region_name=REGION)
    except Exception as error:
        log("SES CLIENT ERROR:", type(error).__name__)
        return False

    sent = False
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
            # Message ID only: recipient addresses are not written to logs.
            log("EMAIL SENT MESSAGE_ID:", result.get("MessageId"))
            sent = True
        except Exception as error:
            # SES sandbox rejects unverified recipients; others may still succeed.
            log("EMAIL SEND ERROR:", type(error).__name__)
    return sent


def admin_recipients():
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
        f"Status: {lc.effective_status(complaint)}",
    ]
    if complaint.get("assignedOfficerName"):
        lines.append(f"Assigned officer: {complaint['assignedOfficerName']}")
    if include_sla and complaint.get("createdAt"):
        lines.append(f"SLA: {lc.sla_hours_of(complaint)} hours")
    lines.extend(["", "This is an automated notification from CivicTrack."])
    return "\n".join(lines)


def record_citizen_notification(complaint_id, kind, sent):
    """Store whether the citizen-facing email was accepted by SES."""
    status = {"kind": kind, "sent": bool(sent), "at": utcnow().isoformat()}
    try:
        get_table().update_item(
            Key={"complaintId": complaint_id},
            UpdateExpression="SET lastCitizenNotification = :n",
            ConditionExpression="attribute_exists(complaintId)",
            ExpressionAttributeValues={":n": status},
        )
    except Exception as error:
        log("NOTIFICATION STATUS WRITE ERROR:", type(error).__name__)
    return status


def notify_citizen(complaint, kind, subject, message):
    sent = send_notification([complaint.get("citizenEmail", "")], subject, complaint_email_body(complaint, message))
    complaint["lastCitizenNotification"] = record_citizen_notification(complaint["complaintId"], kind, sent)
    return sent


def notify_complaint_created(complaint):
    cid = complaint["complaintId"]
    notify_citizen(complaint, "created", f"CivicTrack complaint registered: {cid}",
                   "Your CivicTrack complaint has been registered successfully.")
    send_notification(admin_recipients(), f"New CivicTrack complaint: {cid}",
                      complaint_email_body(complaint, "A new citizen complaint has been submitted."))


def notify_complaint_assigned(complaint):
    cid = complaint["complaintId"]
    notify_citizen(complaint, "assigned", f"CivicTrack update: {cid} assigned",
                   "Your complaint has been assigned to an officer.")
    send_notification([get_officer_email(complaint.get("assignedOfficerSub", ""))],
                      f"New complaint assigned to you: {cid}",
                      complaint_email_body(complaint, "A complaint has been assigned to you. Please review it in the CivicTrack Officer Dashboard."))


def notify_work_started(complaint):
    notify_citizen(complaint, "started", f"CivicTrack update: work started on {complaint['complaintId']}",
                   "An officer has started working on your complaint.")


def notify_complaint_resolved(complaint):
    cid = complaint["complaintId"]
    notify_citizen(complaint, "resolved", f"CivicTrack complaint resolved: {cid}",
                   "Your complaint has been marked as resolved. Resolution proof was uploaded by the assigned officer. "
                   "If the issue persists you can reopen it from the Citizen Portal.")
    send_notification(admin_recipients(), f"CivicTrack complaint resolved: {cid}",
                      complaint_email_body(complaint, "An officer has resolved this complaint."))


def notify_complaint_reopened(complaint):
    cid = complaint["complaintId"]
    recipients = admin_recipients() + [get_officer_email(complaint.get("assignedOfficerSub", ""))]
    send_notification(recipients, f"CivicTrack complaint reopened: {cid}",
                      complaint_email_body(complaint, "The citizen has reopened this complaint. A new SLA period has started."))


# --------------------------------------------------
# AUTHENTICATION CONTEXT
# --------------------------------------------------

def get_authorizer_claims(event):
    """Claims supplied by API Gateway after JWT validation (REST or HTTP API)."""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = authorizer.get("claims")
    if isinstance(claims, dict):
        return claims
    claims = (authorizer.get("jwt") or {}).get("claims")
    return claims if isinstance(claims, dict) else None


def get_user_groups(claims):
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
        # REST API authorizer may render the list as "[Admin Officer]" or "Admin,Officer".
        return [g.strip() for g in re.split(r"[,\s]+", groups.strip("[]")) if g.strip()]
    return []


def authenticate_request(event):
    """Identify a verified Cognito user, or an explicitly enabled SAM-local demo user."""
    if LOCAL_DEMO_AUTH_ENABLED:
        headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
        demo_role = headers.get("x-local-demo-role", "")
        demo_sub = headers.get("x-local-demo-sub", "")
        demo_email = headers.get("x-local-demo-email", "")
        if demo_role not in VALID_ROLES or not isinstance(demo_sub, str) or not demo_sub.strip():
            raise RequestError(401, "Valid local demo identity required")
        return {
            "sub": demo_sub.strip()[:128],
            "email": demo_email.strip()[:254] if isinstance(demo_email, str) else "",
            "role": demo_role,
            "name": f"Demo {demo_role}",
        }

    claims = get_authorizer_claims(event)
    if not claims:
        raise RequestError(401, "Authentication required")
    user_sub = claims.get("sub")
    if not user_sub:
        raise RequestError(401, "Invalid authentication identity")
    groups = get_user_groups(claims)
    for role in ("Admin", "Officer", "Citizen"):
        if role in groups:
            break
    else:
        raise RequestError(403, "Your account does not have a CivicTrack role")
    return {
        "sub": user_sub,
        "email": claims.get("email", ""),
        "role": role,
        "name": claims.get("name") or claims.get("email", ""),
    }


# --------------------------------------------------
# COMPLAINT ACCESS & SERIALIZATION
# --------------------------------------------------

def load_complaint(complaint_id, table=None):
    item = (table or get_table()).get_item(Key={"complaintId": complaint_id}, ConsistentRead=True).get("Item")
    if not item:
        raise RequestError(404, "Complaint not found")
    return item


def can_view(complaint, user):
    if user["role"] == "Admin":
        return True
    if user["role"] == "Citizen":
        return complaint.get("citizenSub") == user["sub"]
    if user["role"] == "Officer":
        return bool(complaint.get("assignedOfficerSub")) and complaint.get("assignedOfficerSub") == user["sub"]
    return False


def require_complaint_access(complaint, user):
    """Return 404 (not 403) to other citizens so complaint IDs cannot be probed."""
    if can_view(complaint, user):
        return
    if user["role"] == "Officer":
        raise RequestError(403, "Complaint is not assigned to you")
    raise RequestError(404, "Complaint not found")


def serialize_complaint(item, user, include_urls=True, now=None):
    data = dict(item)
    data["effectiveStatus"] = lc.effective_status(item)
    data["department"] = lc.department_for(item)
    data["priority"] = lc.normalize_priority(item.get("priority")) or "MEDIUM"
    data["sla"] = lc.sla_state(item, now or utcnow(), SLA_WARNING_HOURS)
    if include_urls:
        for key_field, url_field in (("photoKey", "photoUrl"), ("proofPhotoKey", "proofPhotoUrl"), ("beforePhotoKey", "beforePhotoUrl")):
            if item.get(key_field):
                data[url_field] = create_presigned_view_url(item[key_field])
    if user["role"] == "Citizen":
        # Internal identifiers of staff are not needed by citizens.
        for field in ("assignedBySub", "resolvedBySub", "assignedOfficerSub"):
            data.pop(field, None)
        data["history"] = [
            {k: v for k, v in entry.items() if k != "actorSub"} for entry in item.get("history", [])
        ]
    return data


def history_entry(action, user, from_status, to_status, note=None, **extra):
    entry = {
        "at": utcnow().isoformat(),
        "action": action,
        "actorRole": user["role"],
        "actorSub": user["sub"],
        "fromStatus": from_status,
        "toStatus": to_status,
    }
    if note:
        entry["note"] = note
    entry.update({k: v for k, v in extra.items() if v not in (None, "")})
    return entry


def guard_condition(item):
    """Optimistic-concurrency guard: the update applies only if status and
    assigned officer are unchanged since ``item`` was read."""
    values = {":g_status": item.get("status") or lc.PENDING}
    condition = "attribute_exists(complaintId) AND "
    if item.get("status"):
        condition += "#status = :g_status"
    else:
        condition += "(attribute_not_exists(#status) OR #status = :g_status)"
    officer = item.get("assignedOfficerSub") or ""
    if officer:
        condition += " AND assignedOfficerSub = :g_officer"
        values[":g_officer"] = officer
    else:
        condition += " AND (attribute_not_exists(assignedOfficerSub) OR assignedOfficerSub = :g_empty)"
        values[":g_empty"] = ""
    return condition, values


def apply_transition(table, item, set_fields, entry, remove_fields=()):
    """Conditionally update a complaint and append an audit entry."""
    names = {"#status": "status", "#history": "history"}
    values = {":entry": [entry], ":emptyList": []}
    assignments = ["#history = list_append(if_not_exists(#history, :emptyList), :entry)"]
    for index, (field, value) in enumerate(set_fields.items()):
        names[f"#f{index}"] = field
        values[f":v{index}"] = value
        assignments.append(f"#f{index} = :v{index}")
    expression = "SET " + ", ".join(assignments)
    if remove_fields:
        expression += " REMOVE " + ", ".join(remove_fields)
    condition, guard_values = guard_condition(item)
    values.update(guard_values)
    if len(item.get("history", [])) >= MAX_HISTORY:
        raise RequestError(409, "Complaint history limit reached; contact an administrator")
    try:
        result = table.update_item(
            Key={"complaintId": item["complaintId"]},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise RequestError(409, "Complaint was changed by another request; refresh and retry")
        raise
    return result.get("Attributes", {})


def require_transition(action, item):
    current = lc.effective_status(item)
    if not lc.can_transition(action, current):
        allowed = ", ".join(sorted(lc.TRANSITIONS[action][0]))
        raise RequestError(409, f"Cannot {action} a complaint that is {current}; it must be {allowed}")
    return current


# --------------------------------------------------
# CREATE / LIST / DETAIL
# --------------------------------------------------

def parse_coordinate(body, field, low, high):
    value = body.get(field)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise RequestError(400, f"{field} must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RequestError(400, f"{field} must be a number")
    if not number.is_finite() or number < low or number > high:
        raise RequestError(400, f"{field} must be between {low} and {high}")
    return number.quantize(Decimal("0.000001"))


def create_complaint(event, user):
    if user["role"] != "Citizen":
        raise RequestError(403, "Only citizens can register complaints")
    body = parse_body(event)

    fields = {}
    for name, limit in MAX_TEXT.items():
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            raise RequestError(400, f"{name} is required")
        value = value.strip()
        if len(value) > limit:
            raise RequestError(400, f"{name} must be {limit} characters or fewer")
        fields[name] = value

    latitude = parse_coordinate(body, "latitude", -90, 90)
    longitude = parse_coordinate(body, "longitude", -180, 180)
    if (latitude is None) != (longitude is None):
        raise RequestError(400, "latitude and longitude must be provided together")
    accuracy = parse_coordinate(body, "locationAccuracy", 0, 100000)
    landmark = optional_text(body, "landmark", 150)

    photo_key = body.get("photoKey")
    if photo_key is not None:
        valid, message = validate_uploaded_image(photo_key, f'citizens/{user["sub"]}/complaint-evidence/')
        if not valid:
            raise RequestError(400, message)

    # Idempotency: a retried submission with the same clientRequestId maps to the same complaint.
    request_id = body.get("clientRequestId")
    if request_id is not None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_id):
            raise RequestError(400, "Invalid clientRequestId")
        digest = hashlib.sha256(f'{user["sub"]}:{request_id}'.encode()).hexdigest()[:10].upper()
        complaint_id = f"CT-{digest}"
    else:
        complaint_id = f"CT-{uuid.uuid4().hex[:10].upper()}"

    now = utcnow().isoformat()
    complaint = {
        "complaintId": complaint_id,
        **fields,
        "photoKey": photo_key or "",
        "citizenSub": user["sub"],
        "citizenEmail": user["email"],
        "assignedOfficerSub": "",
        "assignedOfficerName": "",
        "status": lc.PENDING,
        "priority": "MEDIUM",
        "department": lc.department_for({"category": fields["category"]}),
        "createdAt": now,
        "slaHours": lc.DEFAULT_SLA_HOURS,
        "escalated": False,
        "reopenCount": 0,
        "history": [history_entry("created", user, None, lc.PENDING)],
    }
    if latitude is not None:
        complaint["latitude"] = latitude
        complaint["longitude"] = longitude
        if accuracy is not None:
            complaint["locationAccuracy"] = accuracy
    if landmark:
        complaint["landmark"] = landmark

    table = get_table()
    try:
        table.put_item(Item=complaint, ConditionExpression="attribute_not_exists(complaintId)")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = load_complaint(complaint_id, table)
        if existing.get("citizenSub") != user["sub"]:
            raise RequestError(409, "Please retry the submission")
        return response(200, {"message": "Complaint already registered", "complaint": serialize_complaint(existing, user), "duplicate": True})

    log("COMPLAINT CREATED:", complaint_id)
    notify_complaint_created(complaint)  # email failures never roll back the complaint
    return response(201, {"message": "Complaint registered successfully", "complaint": serialize_complaint(complaint, user)})


def get_complaints(user):
    table = get_table()
    scan_arguments = {"ConsistentRead": True}
    if user["role"] == "Citizen":
        scan_arguments["FilterExpression"] = Attr("citizenSub").eq(user["sub"])
    elif user["role"] == "Officer":
        scan_arguments["FilterExpression"] = Attr("assignedOfficerSub").eq(user["sub"])
    elif user["role"] != "Admin":
        raise RequestError(403, "Insufficient permissions")

    items, last_key = [], None
    while True:
        if last_key:
            scan_arguments["ExclusiveStartKey"] = last_key
        result = table.scan(**scan_arguments)
        items.extend(result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

    now = utcnow()
    complaints = [serialize_complaint(item, user, now=now) for item in items if can_view(item, user)]
    complaints.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
    log("LIST COMPLAINTS:", user["role"], len(complaints))
    return response(200, {"count": len(complaints), "complaints": complaints, "serverTime": now.isoformat()})


def get_complaint(user, complaint_id):
    validate_complaint_id(complaint_id)
    complaint = load_complaint(complaint_id)
    require_complaint_access(complaint, user)
    return response(200, {"complaint": serialize_complaint(complaint, user)})


# --------------------------------------------------
# ADMIN ACTIONS
# --------------------------------------------------

def update_complaint_metadata(event, user, complaint_id):
    """Admin: priority, department, zone and ward."""
    if user["role"] != "Admin":
        raise RequestError(403, "Only Admins can update complaint details")
    validate_complaint_id(complaint_id)
    body = parse_body(event)

    updates = {}
    if "priority" in body:
        priority = lc.normalize_priority(body["priority"])
        if not priority:
            raise RequestError(400, f"priority must be one of {', '.join(lc.PRIORITIES)}")
        updates["priority"] = priority
    if "department" in body:
        department = optional_text(body, "department", MAX_AREA)
        if department not in lc.DEPARTMENTS:
            raise RequestError(400, "Unknown department")
        updates["department"] = department
    for field in ("zone", "ward"):
        if field in body:
            updates[field] = optional_text(body, field, MAX_AREA) or ""
    if not updates:
        raise RequestError(400, "No supported fields to update")

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    status = lc.effective_status(complaint)
    changes = {k: v for k, v in updates.items() if complaint.get(k) != v}
    if not changes:
        return response(200, {"message": "No changes", "complaint": serialize_complaint(complaint, user)})
    changes["updatedAt"] = utcnow().isoformat()
    summary = ", ".join(f"{k}={v or '—'}" for k, v in changes.items() if k != "updatedAt")
    updated = apply_transition(table, complaint, changes, history_entry("details_updated", user, status, status, note=summary))
    return response(200, {"message": "Complaint updated", "complaint": serialize_complaint(updated, user)})


def assign_complaint(event, user, complaint_id):
    """Assign a PENDING/REOPENED complaint, or reassign one that has not started."""
    if user["role"] != "Admin":
        raise RequestError(403, "Only Admins can assign complaints")
    validate_complaint_id(complaint_id)
    body = parse_body(event)

    officer_sub = body.get("officerSub")
    if not isinstance(officer_sub, str) or not officer_sub.strip() or len(officer_sub.strip()) > 128:
        raise RequestError(400, "Officer ID is required")
    officer_sub = officer_sub.strip()

    officer_name = body.get("officerName")
    if LOCAL_DEMO_AUTH_ENABLED:
        demo = next((o for o in DEMO_OFFICERS if o["sub"] == officer_sub), None)
        officer_name = demo["name"] if demo else (officer_name.strip()[:150] if isinstance(officer_name, str) and officer_name.strip() else None)
        if not officer_name:
            raise RequestError(400, "Officer name is required")
    else:
        # Assignments must target a real, enabled member of the Cognito Officer group.
        if not USER_POOL_ID:
            raise RequestError(500, "Officer directory is not configured")
        officer = find_officer(officer_sub)
        if not officer or not officer.get("enabled", True):
            raise RequestError(400, "Selected user is not an active member of the CivicTrack Officer group")
        officer_name = officer["name"] or officer["email"] or "Officer"

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    current = lc.effective_status(complaint)
    action = "reassign" if current == lc.ASSIGNED else "assign"
    if current == lc.REOPENED and complaint.get("assignedOfficerSub"):
        action = "assign"  # reopened complaints may go to the same or a different officer
    if current == lc.ASSIGNED and complaint.get("assignedOfficerSub") == officer_sub:
        raise RequestError(409, "Complaint is already assigned to this officer")
    if current == lc.IN_PROGRESS:
        raise RequestError(409, "Work has started; the complaint can no longer be reassigned")
    require_transition(action, complaint)

    reason = optional_text(body, "reason", MAX_NOTE)
    now = utcnow().isoformat()
    updated = apply_transition(
        table,
        complaint,
        {
            "status": lc.ASSIGNED,
            "assignedOfficerSub": officer_sub,
            "assignedOfficerName": officer_name,
            "assignedAt": now,
            "assignedBySub": user["sub"],
        },
        history_entry(action + "ed" if action == "assign" else "reassigned", user, current, lc.ASSIGNED,
                      note=reason, officerName=officer_name,
                      previousOfficerName=complaint.get("assignedOfficerName") if action == "reassign" else None),
    )
    log("ASSIGN:", complaint_id, action)
    notify_complaint_assigned(updated)
    return response(200, {"message": "Complaint reassigned successfully" if action == "reassign" else "Complaint assigned successfully",
                          "complaint": serialize_complaint(updated, user)})


def unassign_complaint(event, user, complaint_id):
    if user["role"] != "Admin":
        raise RequestError(403, "Only Admins can unassign complaints")
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=False)
    reason = optional_text(body, "reason", MAX_NOTE)
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    current = require_transition("unassign", complaint)
    updated = apply_transition(
        table,
        complaint,
        {"status": lc.PENDING, "assignedOfficerSub": "", "assignedOfficerName": "", "unassignedAt": utcnow().isoformat()},
        history_entry("unassigned", user, current, lc.PENDING, note=reason,
                      previousOfficerName=complaint.get("assignedOfficerName")),
    )
    return response(200, {"message": "Officer unassigned", "complaint": serialize_complaint(updated, user)})


# --------------------------------------------------
# OFFICER WORKFLOW
# --------------------------------------------------

def update_officer_workflow(event, user, complaint_id, action):
    """Only the assigned officer can start or resolve their complaint."""
    if user["role"] != "Officer":
        raise RequestError(403, "Only officers can update complaint progress")
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=(action == "resolve"))
    note = optional_text(body, "note", MAX_NOTE)

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    if complaint.get("assignedOfficerSub") != user["sub"]:
        raise RequestError(403, "Complaint is not assigned to you")
    current = require_transition(action, complaint)
    now = utcnow().isoformat()

    if action == "start":
        fields = {"status": lc.IN_PROGRESS, "workStartedAt": now}
        before_key = body.get("beforePhotoKey")
        if before_key is not None:
            valid, message = validate_uploaded_image(before_key, officer_media_prefix(user["sub"], complaint_id, "before"))
            if not valid:
                raise RequestError(400, message)
            fields["beforePhotoKey"] = before_key
        updated = apply_transition(table, complaint, fields, history_entry("work_started", user, current, lc.IN_PROGRESS, note=note))
        notify_work_started(updated)
        return response(200, {"message": "Work started successfully", "complaint": serialize_complaint(updated, user)})

    proof_key = body.get("proofPhotoKey")
    if not isinstance(proof_key, str) or not proof_key.strip():
        raise RequestError(400, "A valid proofPhotoKey is required")
    proof_key = proof_key.strip()
    if not validate_object_key(proof_key):
        raise RequestError(400, "Invalid proofPhotoKey")
    valid, message = validate_uploaded_image(proof_key, officer_media_prefix(user["sub"], complaint_id, "after"))
    if not valid:
        raise RequestError(400, message)
    fields = {"status": lc.RESOLVED, "resolvedAt": now, "resolvedBySub": user["sub"], "proofPhotoKey": proof_key}
    if note:
        fields["resolutionNote"] = note
    updated = apply_transition(table, complaint, fields, history_entry("resolved", user, current, lc.RESOLVED, note=note))
    notify_complaint_resolved(updated)
    return response(200, {"message": "Complaint resolved successfully", "complaint": serialize_complaint(updated, user)})


# --------------------------------------------------
# CITIZEN FEEDBACK: REOPEN / CLOSE
# --------------------------------------------------

def reopen_complaint(event, user, complaint_id):
    if user["role"] != "Citizen":
        raise RequestError(403, "Only the citizen who reported a complaint can reopen it")
    validate_complaint_id(complaint_id)
    body = parse_body(event)
    reason = optional_text(body, "reason", MAX_NOTE)
    if not reason or len(reason) < 5:
        raise RequestError(400, "Please describe why the issue is not resolved (at least 5 characters)")
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    require_complaint_access(complaint, user)
    current = require_transition("reopen", complaint)
    now = utcnow().isoformat()
    updated = apply_transition(
        table,
        complaint,
        {
            "status": lc.REOPENED,
            "reopenedAt": now,
            "slaStartedAt": now,  # a fresh SLA period applies to the reopened work
            "reopenCount": int(complaint.get("reopenCount", 0) or 0) + 1,
            "escalated": False,
        },
        history_entry("reopened", user, current, lc.REOPENED, note=reason),
        remove_fields=("slaWarningSentAt", "escalationNotifiedAt", "escalatedAt"),
    )
    notify_complaint_reopened(updated)
    return response(200, {"message": "Complaint reopened", "complaint": serialize_complaint(updated, user)})


def close_complaint(event, user, complaint_id):
    if user["role"] not in ("Citizen", "Admin"):
        raise RequestError(403, "Only the reporting citizen or an Admin can close a complaint")
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=False)
    note = optional_text(body, "note", MAX_NOTE)
    rating = body.get("rating")
    if rating is not None and (not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5):
        raise RequestError(400, "rating must be an integer from 1 to 5")
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    require_complaint_access(complaint, user)
    current = require_transition("close", complaint)
    fields = {"status": lc.CLOSED, "closedAt": utcnow().isoformat(), "closedByRole": user["role"]}
    if rating is not None and user["role"] == "Citizen":
        fields["citizenRating"] = rating
    updated = apply_transition(table, complaint, fields, history_entry("closed", user, current, lc.CLOSED, note=note))
    return response(200, {"message": "Complaint closed", "complaint": serialize_complaint(updated, user)})


# --------------------------------------------------
# SLA AUTOMATION (EventBridge, every 15 minutes)
# --------------------------------------------------

SLA_SWEEP_STATUSES = sorted(lc.OPEN_STATUSES)


def claim_flag(table, complaint_id, flag, stamp):
    """Atomically claim a one-time notification. Exactly one concurrent
    invocation wins; the others see ConditionalCheckFailed and skip."""
    try:
        table.update_item(
            Key={"complaintId": complaint_id},
            UpdateExpression=f"SET {flag} = :now",
            ConditionExpression=f"attribute_exists(complaintId) AND attribute_not_exists({flag}) AND #status IN (:s0, :s1, :s2, :s3)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":now": stamp, **{f":s{i}": s for i, s in enumerate(SLA_SWEEP_STATUSES)}},
        )
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def release_flag(table, complaint_id, flag, stamp):
    """Undo our own claim when sending failed so the next sweep retries."""
    try:
        table.update_item(
            Key={"complaintId": complaint_id},
            UpdateExpression=f"REMOVE {flag}",
            ConditionExpression=f"{flag} = :now",
            ExpressionAttributeValues={":now": stamp},
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def mark_escalated(table, complaint_id, stamp):
    entry = {"at": stamp, "action": "sla_escalated", "actorRole": "System", "actorSub": "sla-checker",
             "fromStatus": None, "toStatus": None, "note": "SLA deadline breached"}
    try:
        table.update_item(
            Key={"complaintId": complaint_id},
            UpdateExpression="SET escalated = :yes, escalatedAt = :now, #history = list_append(if_not_exists(#history, :emptyList), :entry)",
            ConditionExpression="attribute_exists(complaintId) AND (attribute_not_exists(escalated) OR escalated = :no) AND #status IN (:s0, :s1, :s2, :s3)",
            ExpressionAttributeNames={"#status": "status", "#history": "history"},
            ExpressionAttributeValues={":yes": True, ":no": False, ":now": stamp, ":entry": [entry], ":emptyList": [],
                                       **{f":s{i}": s for i, s in enumerate(SLA_SWEEP_STATUSES)}},
        )
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def process_sla_checks(now=None):
    """Send one warning in the final SLA_WARNING_HOURS and one escalation after
    the deadline. Safe under EventBridge duplicate delivery, Lambda retries and
    concurrent executions (claim-then-send). One bad record never aborts the sweep."""
    table = get_table()
    now = now or utcnow()
    stamp = now.isoformat()
    scan_args = {
        "FilterExpression": Attr("status").is_in(SLA_SWEEP_STATUSES),
        "ProjectionExpression": (
            "complaintId, #status, title, category, #loc, citizenEmail, assignedOfficerSub, "
            "assignedOfficerName, createdAt, slaStartedAt, slaHours, escalated, slaWarningSentAt, escalationNotifiedAt"
        ),
        "ExpressionAttributeNames": {"#status": "status", "#loc": "location"},
    }
    summary = {"status": "completed", "scanned": 0, "warningsSent": 0, "newEscalations": 0,
               "notificationEmailsSent": 0, "malformedTimestamps": 0, "errors": 0, "checkedAt": stamp}
    officer_cache = {}
    admins = None
    last_key = None

    while True:
        if last_key:
            scan_args["ExclusiveStartKey"] = last_key
        result = table.scan(**scan_args)
        for complaint in result.get("Items", []):
            summary["scanned"] += 1
            complaint_id = complaint.get("complaintId")
            if not complaint_id:
                continue
            try:
                start = lc.sla_clock_start(complaint)
                if not start:
                    summary["malformedTimestamps"] += 1
                    continue
                hours = lc.sla_hours_of(complaint)
                deadline = start + timedelta(hours=hours)
                warning_at = deadline - timedelta(hours=min(SLA_WARNING_HOURS, hours))

                if now >= deadline:
                    if not complaint.get("escalated") and mark_escalated(table, complaint_id, stamp):
                        summary["newEscalations"] += 1
                    flag, subject, text = (
                        "escalationNotifiedAt",
                        f"CivicTrack SLA BREACHED: {complaint_id}",
                        f"The {hours}-hour SLA has been breached. This complaint has been escalated and requires immediate attention.",
                    )
                elif now >= warning_at:
                    flag, subject, text = (
                        "slaWarningSentAt",
                        f"CivicTrack SLA warning: {complaint_id}",
                        f"This complaint is approaching its {hours}-hour SLA deadline. Please take action before {deadline.isoformat()}.",
                    )
                else:
                    continue

                if complaint.get(flag) or not claim_flag(table, complaint_id, flag, stamp):
                    continue
                if admins is None:
                    admins = admin_recipients()
                recipients = [get_officer_email(complaint.get("assignedOfficerSub", ""), officer_cache)] + admins
                if send_notification(recipients, subject, complaint_email_body(complaint, text)):
                    summary["notificationEmailsSent"] += 1
                    if flag == "slaWarningSentAt":
                        summary["warningsSent"] += 1
                else:
                    release_flag(table, complaint_id, flag, stamp)
            except Exception as error:
                summary["errors"] += 1
                log("SLA CHECK ERROR:", complaint_id, type(error).__name__)
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

    log("SLA CHECK SUMMARY:", json.dumps(summary))
    return summary


# --------------------------------------------------
# REQUEST ROUTER
# --------------------------------------------------

ID = r"([^/]+)"
ROUTES = [
    # (pattern, {method: handler(event, user, *groups)})
    (r"/evidence/upload-url", {"POST": lambda e, u: create_citizen_upload_url(e, u)}),
    (r"/officers", {"GET": lambda e, u: list_officers(u)}),
    (r"/complaints", {"GET": lambda e, u: get_complaints(u), "POST": lambda e, u: create_complaint(e, u)}),
    (rf"/complaints/{ID}", {"GET": lambda e, u, c: get_complaint(u, c), "PATCH": lambda e, u, c: update_complaint_metadata(e, u, c)}),
    (rf"/complaints/{ID}/proof-upload-url", {"POST": lambda e, u, c: create_officer_proof_upload_url(e, u, c)}),
    (rf"/complaints/{ID}/assign", {"PATCH": lambda e, u, c: assign_complaint(e, u, c)}),
    (rf"/complaints/{ID}/unassign", {"PATCH": lambda e, u, c: unassign_complaint(e, u, c)}),
    (rf"/complaints/{ID}/start", {"PATCH": lambda e, u, c: update_officer_workflow(e, u, c, "start")}),
    (rf"/complaints/{ID}/resolve", {"PATCH": lambda e, u, c: update_officer_workflow(e, u, c, "resolve")}),
    (rf"/complaints/{ID}/reopen", {"PATCH": lambda e, u, c: reopen_complaint(e, u, c)}),
    (rf"/complaints/{ID}/close", {"PATCH": lambda e, u, c: close_complaint(e, u, c)}),
]
COMPILED_ROUTES = [(re.compile(pattern), handlers) for pattern, handlers in ROUTES]


def lambda_handler(event, context):
    event = event or {}
    # EventBridge scheduled SLA sweep (not an API Gateway request).
    if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
        return process_sla_checks()

    method = (event.get("httpMethod") or (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")).upper()
    path = event.get("path") or event.get("rawPath", "")
    normalized_path = path.rstrip("/") or "/"

    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})
    if normalized_path == "/health" and method == "GET":
        return response(200, {"status": "healthy", "application": "CivicTrack API"})

    for pattern, handlers in COMPILED_ROUTES:
        match = pattern.fullmatch(normalized_path)
        if not match:
            continue
        try:
            user = authenticate_request(event)
            handler = handlers.get(method)
            if not handler:
                return response(405, {"error": "Method not allowed"})
            args = [unquote(group) for group in match.groups()]
            return handler(event, user, *args)
        except RequestError as error:
            return response(error.status_code, {"error": error.message})
        except Exception as error:
            log("UNHANDLED ERROR:", method, pattern.pattern, type(error).__name__)
            return response(500, {"error": "Internal server error"})

    return response(404, {"error": "Route not found"})
