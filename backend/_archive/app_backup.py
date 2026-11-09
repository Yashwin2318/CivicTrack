

import json

import os

import re

import uuid

from datetime import datetime, timezone

from urllib.parse import unquote

import boto3

from boto3.dynamodb.conditions import Attr

from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get(

    "COMPLAINTS_TABLE",

    "CivicTrack-Complaints"

)

LOCAL_MODE = os.environ.get("AWS_SAM_LOCAL") == "true"

ALLOWED_ORIGIN = os.environ.get(

    "ALLOWED_ORIGIN",

    "http://localhost:5173"

)

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

def response(status_code, body):

    return {

        "statusCode": status_code,

        "headers": {

            "Content-Type": "application/json",

            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,

            "Access-Control-Allow-Headers": "Content-Type,Authorization",

            "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",

            "Vary": "Origin"

        },

        "body": json.dumps(body, default=str)

    }

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

    """Identify the authenticated user and determine their role."""

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

        now = datetime.now(timezone.utc).isoformat()

        complaint = {

            "complaintId": f"CT-{uuid.uuid4().hex[:10].upper()}",

            "title": title,

            "category": category,

            "location": location,

            "description": description,

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
        if (proof_key.startswith(("/", "\\")) or ".." in proof_key.split("/")
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
        return response(200, {
            "message": "Work started successfully" if action == "start"
                       else "Complaint resolved successfully",
            "complaint": result.get("Attributes", {}),
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
