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

import base64

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

import analytics_engine
import geo
import intelligence
import lifecycle as lc
import reference

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

TABLE_NAME = os.environ.get("COMPLAINTS_TABLE", "CivicTrack-Complaints")
CONFIG_TABLE_NAME = os.environ.get("CONFIG_TABLE", "CivicTrack-Config")
CITIZEN_INDEX = os.environ.get("CITIZEN_INDEX", "citizenSub-createdAt-index")
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


_request_context = {"requestId": None}


def log(*parts, level="INFO", **fields):
    """Structured JSON log line (CloudWatch Logs Insights friendly).
    Never pass email addresses, tokens or free-text complaint content here."""
    record = {"level": level, "message": " ".join(str(p) for p in parts)}
    if _request_context["requestId"]:
        record["requestId"] = _request_context["requestId"]
    record.update(fields)
    print(json.dumps(record, default=str), flush=True)


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
            "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,OPTIONS",
            "Vary": "Origin",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=_json_default),
    }


ERROR_CODES = {400: "VALIDATION_ERROR", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 501: "NOT_CONFIGURED"}


def error_response(status_code, message):
    """Uniform error shape: {"error": message, "code": MACHINE_CODE}."""
    return response(status_code, {"error": message, "code": ERROR_CODES.get(status_code, "ERROR")})


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

def _dynamodb():
    if LOCAL_MODE:
        return boto3.resource(
            "dynamodb",
            endpoint_url="http://host.docker.internal:8000",
            region_name="ap-south-1",
            aws_access_key_id="dummy",
            aws_secret_access_key="dummy",
        )
    return boto3.resource("dynamodb", region_name=REGION)


def get_table():
    return _dynamodb().Table(TABLE_NAME)


def get_config_table():
    return _dynamodb().Table(CONFIG_TABLE_NAME)


def load_reference():
    """Reference data; falls back to built-in defaults if the config table is unavailable."""
    try:
        return reference.load(get_config_table())
    except ClientError as error:
        log("REFERENCE LOAD FAILED; using built-in defaults", level="WARN", errorCode=error.response.get("Error", {}).get("Code"))
        return {"departments": reference.builtin_departments(), "categories": reference.builtin_categories(), "zones": [], "wards": [],
                "officers": {}, "settings": dict(reference.DEFAULT_SETTINGS), "source": "builtin-fallback"}


def audit(user, action, target, details=None):
    """Best-effort administrative audit log; never fails the request."""
    try:
        reference.write_audit(get_config_table(), user, action, target, details)
    except Exception as error:
        log("AUDIT WRITE FAILED", level="WARN", errorType=type(error).__name__, action=action)


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
    folder = {"before": "before", "progress": "progress"}.get(stage, "proof")
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
    if stage not in ("before", "after", "progress"):
        raise RequestError(400, "stage must be 'before', 'progress' or 'after'")

    complaint = load_complaint(complaint_id)
    require_complaint_access(complaint, user)
    status = lc.effective_status(complaint)
    if stage in ("after", "progress") and status != lc.IN_PROGRESS:
        raise RequestError(409, "Start work before uploading progress or resolution photos")
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


OFFICER_PROFILE_DEFAULTS = {"departmentId": "", "zoneIds": [], "wardIds": [], "available": True, "maxActive": 15}


def officer_profile(ref, sub):
    return {**OFFICER_PROFILE_DEFAULTS, **{k: v for k, v in ref["officers"].get(sub, {}).items() if k in OFFICER_PROFILE_DEFAULTS}}


def list_officers(user):
    if user["role"] != "Admin":
        raise RequestError(403, "Only Admins can view the officer directory")
    if LOCAL_DEMO_AUTH_ENABLED:
        members, source = DEMO_OFFICERS, "local-demo"
    else:
        if not USER_POOL_ID:
            raise RequestError(500, "Officer directory is not configured")
        members, source = list_group_users("Officer"), "cognito"
    ref = load_reference()
    officers = [
        {**{k: o[k] for k in ("sub", "email", "name", "enabled", "status")}, "profile": officer_profile(ref, o["sub"])}
        for o in members if o["sub"]
    ]
    officers.sort(key=lambda o: (o["name"] or o["email"]).lower())
    return response(200, {"officers": officers, "source": source})


def update_officer_profile(event, user, officer_sub):
    """Admin: department, zones, wards, availability and workload cap for an officer."""
    require_admin(user)
    if not find_officer(officer_sub) and not LOCAL_DEMO_AUTH_ENABLED:
        raise RequestError(404, "Officer not found in the Officer group")
    body = parse_body(event)
    ref = load_reference()
    profile = officer_profile(ref, officer_sub)
    if "departmentId" in body:
        dept_id = body["departmentId"] or ""
        if dept_id and not reference.department_by_id(ref, dept_id):
            raise RequestError(400, "Unknown department")
        profile["departmentId"] = dept_id
    for field, collection in (("zoneIds", ref["zones"]), ("wardIds", ref["wards"])):
        if field in body:
            values = body[field]
            known = {a["id"] for a in collection}
            if not isinstance(values, list) or len(values) > 50 or any(v not in known for v in values):
                raise RequestError(400, f"{field} must list existing {field[:-3]} IDs")
            profile[field] = sorted(set(values))
    if "available" in body:
        if not isinstance(body["available"], bool):
            raise RequestError(400, "available must be true or false")
        profile["available"] = body["available"]
    if "maxActive" in body:
        if not isinstance(body["maxActive"], int) or isinstance(body["maxActive"], bool) or not 1 <= body["maxActive"] <= 200:
            raise RequestError(400, "maxActive must be an integer from 1 to 200")
        profile["maxActive"] = body["maxActive"]
    reference.put_item(get_config_table(), "OFFICER", officer_sub, {**profile, "updatedAt": utcnow().isoformat()})
    audit(user, "officer_profile_updated", officer_sub, profile)
    return response(200, {"profile": profile})


# --------------------------------------------------
# USER ADMINISTRATION (Cognito)
# --------------------------------------------------

CIVICTRACK_GROUPS = ("Citizen", "Officer", "Admin")


def require_admin(user):
    if user["role"] != "Admin":
        raise RequestError(403, "Administrator role required")


def _user_record(member, role=None):
    attrs = {a.get("Name"): a.get("Value", "") for a in member.get("Attributes", member.get("UserAttributes", []))}
    return {"username": member.get("Username", ""), "sub": attrs.get("sub", ""), "email": attrs.get("email", ""),
            "name": attrs.get("name", ""), "enabled": bool(member.get("Enabled", True)),
            "status": member.get("UserStatus", ""), "role": role}


def list_users(event, user):
    require_admin(user)
    params = event.get("queryStringParameters") or {}
    group = params.get("role", "Officer")
    if group not in CIVICTRACK_GROUPS:
        raise RequestError(400, "role must be Citizen, Officer or Admin")
    if LOCAL_DEMO_AUTH_ENABLED or not USER_POOL_ID:
        raise RequestError(501, "User administration requires the Cognito user pool")
    args = {"UserPoolId": USER_POOL_ID, "GroupName": group, "Limit": 60}
    if params.get("nextToken"):
        args["NextToken"] = params["nextToken"][:2048]
    result = get_cognito_client().list_users_in_group(**args)
    users = [_user_record(m, group) for m in result.get("Users", [])]
    return response(200, {"users": users, "nextToken": result.get("NextToken")})


def create_user(event, user):
    """Invite an Officer or Admin. Cognito emails a temporary password."""
    require_admin(user)
    body = parse_body(event)
    email = (body.get("email") or "").strip().lower()
    name = optional_text(body, "name", 100) or ""
    role = body.get("role")
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
        raise RequestError(400, "A valid email address is required")
    if role not in ("Officer", "Admin"):
        raise RequestError(400, "role must be Officer or Admin (citizens self-register)")
    if not name:
        raise RequestError(400, "name is required")
    if not USER_POOL_ID:
        raise RequestError(501, "User administration requires the Cognito user pool")
    cognito = get_cognito_client()
    try:
        created = cognito.admin_create_user(
            UserPoolId=USER_POOL_ID, Username=email, DesiredDeliveryMediums=["EMAIL"],
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}, {"Name": "name", "Value": name}],
        )["User"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "UsernameExistsException":
            raise RequestError(409, "A user with this email already exists")
        raise
    cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=created["Username"], GroupName=role)
    record = _user_record(created, role)
    audit(user, "user_created", record["sub"] or created["Username"], {"role": role})
    return response(201, {"user": record, "message": "Invitation email sent by Cognito"})


def _cognito_user(username):
    try:
        found = get_cognito_client().admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "UserNotFoundException":
            raise RequestError(404, "User not found")
        raise
    groups = get_cognito_client().admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=username).get("Groups", [])
    roles = [g["GroupName"] for g in groups if g["GroupName"] in CIVICTRACK_GROUPS]
    return found, roles


def update_user(event, user, username):
    """Change a user's CivicTrack role and/or enable/disable sign-in."""
    require_admin(user)
    if not USER_POOL_ID:
        raise RequestError(501, "User administration requires the Cognito user pool")
    username = username.strip()
    if not username or len(username) > 128:
        raise RequestError(400, "Invalid username")
    body = parse_body(event)
    found, roles = _cognito_user(username)
    record = _user_record(found)
    if record["sub"] == user["sub"]:
        raise RequestError(409, "Administrators cannot change their own role or access")
    cognito = get_cognito_client()
    changes = {}
    if "role" in body:
        role = body["role"]
        if role not in CIVICTRACK_GROUPS:
            raise RequestError(400, "role must be Citizen, Officer or Admin")
        for existing in roles:
            if existing != role:
                cognito.admin_remove_user_from_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=existing)
        if role not in roles:
            cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=role)
        changes["role"] = role
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise RequestError(400, "enabled must be true or false")
        (cognito.admin_enable_user if body["enabled"] else cognito.admin_disable_user)(UserPoolId=USER_POOL_ID, Username=username)
        changes["enabled"] = body["enabled"]
    if not changes:
        raise RequestError(400, "Nothing to update (role or enabled)")
    audit(user, "user_updated", record["sub"] or username, changes)
    return response(200, {"user": {**record, **changes, "role": changes.get("role", roles[0] if roles else None)},
                          "message": "Role changes take effect at the user's next sign-in"})


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


def complaint_email_body(complaint, message, include_sla=True, include_location=True):
    """Plain-text email. Never includes the description, evidence links or
    reporter contact details; citizen emails also omit the location."""
    lines = [
        message,
        "",
        f"Complaint ID: {complaint.get('complaintId', 'N/A')}",
        f"Title: {complaint.get('title', 'N/A')}",
        f"Category: {complaint.get('category', 'N/A')}",
    ]
    if include_location:
        lines.append(f"Location: {complaint.get('location', 'N/A')}")
    lines.append(f"Status: {lc.effective_status(complaint)}")
    if complaint.get("assignedOfficerName"):
        lines.append(f"Assigned officer: {complaint['assignedOfficerName']}")
    if include_sla and complaint.get("createdAt"):
        lines.append(f"SLA: {lc.sla_hours_of(complaint)} hours")
    lines.extend(["", "Sign in to CivicTrack to see full details.", "This is an automated notification from CivicTrack."])
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
    sent = send_notification([complaint.get("citizenEmail", "")], subject, complaint_email_body(complaint, message, include_location=False))
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


def notify_officer_removed(complaint, previous_officer_sub, reason_text):
    send_notification([get_officer_email(previous_officer_sub)], f"CivicTrack: {complaint['complaintId']} {reason_text}",
                      complaint_email_body(complaint, f"This complaint has been {reason_text} and is no longer assigned to you."))


def notify_assignment_response(complaint, accepted, reason=None):
    cid = complaint["complaintId"]
    if accepted:
        notify_citizen(complaint, "accepted", f"CivicTrack update: officer accepted {cid}",
                       "The assigned officer has accepted your complaint and will start work soon.")
    else:
        send_notification(admin_recipients(), f"CivicTrack: assignment declined for {cid}",
                          complaint_email_body(complaint, "The officer declined this assignment. It has returned to the Pending queue for reassignment."))


def notify_complaint_rejected(complaint, reason):
    notify_citizen(complaint, "rejected", f"CivicTrack complaint closed without action: {complaint['complaintId']}",
                   "Your complaint was reviewed and closed without action. Sign in to see the reason given by the administrator.")


def notify_escalated(complaint, level):
    cid = complaint["complaintId"]
    recipients = admin_recipients() + [get_officer_email(complaint.get("assignedOfficerSub", ""))]
    return send_notification(recipients, f"CivicTrack ESCALATION level {level}: {cid}",
                             complaint_email_body(complaint, f"This complaint has been escalated (level {level}) and requires immediate attention."))


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


CITIZEN_HIDDEN_FIELDS = ("assignedBySub", "resolvedBySub", "assignedOfficerSub", "priorityScore", "priorityReasons",
                         "escalationNotifiedAt", "slaWarningSentAt", "escalationLevel2NotifiedAt", "photoMeta", "evidenceMeta")


def serialize_complaint(item, user, include_urls=True, now=None, settings=None):
    settings = settings or reference.DEFAULT_SETTINGS
    data = dict(item)
    data["effectiveStatus"] = lc.effective_status(item)
    data["department"] = lc.department_for(item)
    data["priority"] = lc.normalize_priority(item.get("priority")) or "MEDIUM"
    data["sla"] = lc.sla_state(item, now or utcnow(), int(settings.get("warningHours", SLA_WARNING_HOURS)))
    if include_urls:
        for key_field, url_field in (("photoKey", "photoUrl"), ("proofPhotoKey", "proofPhotoUrl"), ("beforePhotoKey", "beforePhotoUrl")):
            if item.get(key_field):
                data[url_field] = create_presigned_view_url(item[key_field])
        if item.get("progressPhotoKeys"):
            data["progressPhotoUrls"] = [create_presigned_view_url(k) for k in item["progressPhotoKeys"]]
    if user["role"] == "Citizen":
        # Internal identifiers and triage internals are not disclosed to citizens.
        for field in CITIZEN_HIDDEN_FIELDS:
            data.pop(field, None)
        if not settings.get("discloseOfficerName", True):
            data.pop("assignedOfficerName", None)
        data["history"] = [
            {k: v for k, v in entry.items() if k != "actorSub" and (k not in ("officerName", "previousOfficerName") or settings.get("discloseOfficerName", True))}
            for entry in item.get("history", [])
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


NEARBY_RADIUS_M = 200


def scan_all(table, **kwargs):
    items, last_key = [], None
    while True:
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        result = table.scan(**kwargs)
        items.extend(result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            return items


def find_nearby_open(table, latitude, longitude, radius_m=NEARBY_RADIUS_M, category=None, exclude_id=None):
    """Open complaints within radius_m. Full scan of open items: acceptable at
    current volume; see AWS_BUDGET_AUDIT.md for the geohash-index plan."""
    candidates = scan_all(
        table,
        FilterExpression=Attr("status").is_in(sorted(lc.OPEN_STATUSES)) & Attr("latitude").exists(),
        ProjectionExpression="complaintId, #s, category, latitude, longitude, createdAt, citizenSub, assignedOfficerSub, title",
        ExpressionAttributeNames={"#s": "status"},
    )
    found = []
    for item in candidates:
        if item.get("complaintId") == exclude_id or (category and item.get("category") != category):
            continue
        distance = geo.haversine_m(float(latitude), float(longitude), float(item["latitude"]), float(item["longitude"]))
        if distance <= radius_m:
            found.append((round(distance), item))
    return sorted(found, key=lambda pair: pair[0])


def query_citizen_complaints(table, citizen_sub, since_iso=None):
    """Citizen's complaints via the citizenSub GSI; falls back to a filtered
    scan if the index has not been deployed yet."""
    condition = Key("citizenSub").eq(citizen_sub)
    if since_iso:
        condition = condition & Key("createdAt").gte(since_iso)
    try:
        items, kwargs = [], {"IndexName": CITIZEN_INDEX, "KeyConditionExpression": condition, "ScanIndexForward": False}
        while True:
            result = table.query(**kwargs)
            items.extend(result.get("Items", []))
            if not result.get("LastEvaluatedKey"):
                return items
            kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in ("ValidationException", "ResourceNotFoundException"):
            raise
        log("CITIZEN INDEX UNAVAILABLE; falling back to scan", level="WARN")
        filt = Attr("citizenSub").eq(citizen_sub)
        if since_iso:
            filt = filt & Attr("createdAt").gte(since_iso)
        return scan_all(table, FilterExpression=filt)


def triage(ref, table, category, title, description, latitude=None, longitude=None, urgency=None, exclude_id=None):
    """Rule-based routing, priority and location triage (see intelligence.py)."""
    department = reference.route_category(ref, category)
    nearby = find_nearby_open(table, latitude, longitude, category=category, exclude_id=exclude_id) if latitude is not None else []
    scored = intelligence.score_priority(category, title, description, nearby_open=len(nearby), citizen_urgency=urgency)
    ward = zone = None
    if latitude is not None:
        ward = geo.locate(float(longitude), float(latitude), [w for w in ref["wards"] if w.get("active", True)])
        if ward and ward.get("zoneId"):
            zone = next((z for z in ref["zones"] if z["id"] == ward["zoneId"]), None)
        if not zone:
            zone = geo.locate(float(longitude), float(latitude), [z for z in ref["zones"] if z.get("active", True)])
    return {"department": department, "nearby": nearby, "priority": scored, "ward": ward, "zone": zone,
            "suggestion": intelligence.suggest_category(title, description), "summary": intelligence.summarize(title, description)}


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
        if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value):
            raise RequestError(400, f"{name} contains invalid characters")
        fields[name] = value

    ref = load_reference()
    settings = ref["settings"]
    active_categories = {c["name"] for c in ref["categories"] if c.get("active", True)}
    if fields["category"] not in active_categories:
        raise RequestError(400, "Unknown category")

    latitude = parse_coordinate(body, "latitude", -90, 90)
    longitude = parse_coordinate(body, "longitude", -180, 180)
    if (latitude is None) != (longitude is None):
        raise RequestError(400, "latitude and longitude must be provided together")
    accuracy = parse_coordinate(body, "locationAccuracy", 0, 100000)
    landmark = optional_text(body, "landmark", 150)
    urgency = body.get("citizenUrgency")
    if urgency is not None and urgency not in ("LOW", "MEDIUM", "HIGH"):
        raise RequestError(400, "citizenUrgency must be LOW, MEDIUM or HIGH")

    photo_key = body.get("photoKey")
    if photo_key is not None:
        valid, message = validate_uploaded_image(photo_key, f'citizens/{user["sub"]}/complaint-evidence/')
        if not valid:
            raise RequestError(400, message)

    table = get_table()
    # Idempotency: a retried submission with the same clientRequestId maps to the same complaint.
    request_id = body.get("clientRequestId")
    if request_id is not None:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", request_id):
            raise RequestError(400, "Invalid clientRequestId")
        digest = hashlib.sha256(f'{user["sub"]}:{request_id}'.encode()).hexdigest()[:10].upper()
        complaint_id = f"CT-{digest}"
        existing = table.get_item(Key={"complaintId": complaint_id}, ConsistentRead=True).get("Item")
        if existing:
            if existing.get("citizenSub") != user["sub"]:
                raise RequestError(409, "Please retry the submission")
            return response(200, {"message": "Complaint already registered", "complaint": serialize_complaint(existing, user, settings=settings), "duplicate": True})
    else:
        complaint_id = f"CT-{uuid.uuid4().hex[:10].upper()}"

    # Abuse protection: cap new complaints per citizen per hour.
    limit = int(settings.get("maxComplaintsPerHour", 10))
    recent = query_citizen_complaints(table, user["sub"], (utcnow() - timedelta(hours=1)).isoformat())
    if len(recent) >= limit:
        raise RequestError(429, f"You can submit at most {limit} complaints per hour. Please try again later.")

    t = triage(ref, table, fields["category"], fields["title"], fields["description"], latitude, longitude, urgency)
    now = utcnow().isoformat()
    created_entry = history_entry("created", user, None, lc.PENDING)
    complaint = {
        "complaintId": complaint_id,
        **fields,
        "summary": t["summary"],
        "photoKey": photo_key or "",
        "citizenSub": user["sub"],
        "citizenEmail": user["email"],
        "assignedOfficerSub": "",
        "assignedOfficerName": "",
        "status": lc.PENDING,
        "priority": t["priority"]["priority"],
        "priorityScore": t["priority"]["score"],
        "priorityReasons": t["priority"]["reasons"],
        "department": t["department"]["name"],
        "departmentId": t["department"]["id"],
        "createdAt": now,
        "slaHours": int(settings.get("slaHours", lc.DEFAULT_SLA_HOURS)),
        "escalated": False,
        "reopenCount": 0,
        "nearbyOpenCount": len(t["nearby"]),
        "history": [created_entry],
    }
    if urgency:
        complaint["citizenUrgency"] = urgency
    if t["suggestion"]["category"] and t["suggestion"]["category"] != fields["category"]:
        complaint["suggestedCategory"] = t["suggestion"]["category"]
    if latitude is not None:
        complaint["latitude"] = latitude
        complaint["longitude"] = longitude
        if accuracy is not None:
            complaint["locationAccuracy"] = accuracy
        if t["ward"]:
            complaint.update({"wardId": t["ward"]["id"], "ward": t["ward"]["name"]})
        if t["zone"]:
            complaint.update({"zoneId": t["zone"]["id"], "zone": t["zone"]["name"]})
        if t["ward"] or t["zone"]:
            place = t["ward"]["name"] if t["ward"] else t["zone"]["name"]
            created_entry["note"] = f"Located automatically in {place} from boundary data"
    if landmark:
        complaint["landmark"] = landmark

    try:
        table.put_item(Item=complaint, ConditionExpression="attribute_not_exists(complaintId)")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        raise RequestError(409, "Please retry the submission")

    log("COMPLAINT CREATED", complaintId=complaint_id, priority=complaint["priority"])
    notify_complaint_created(complaint)  # email failures never roll back the complaint
    return response(201, {"message": "Complaint registered successfully", "complaint": serialize_complaint(complaint, user, settings=settings)})


def check_similar(event, user):
    """Pre-submission duplicate warning and suggestions. Citizens never see other
    citizens' complaint content: only distance, status and category are returned."""
    body = parse_body(event)
    title = optional_text(body, "title", MAX_TEXT["title"]) or ""
    description = optional_text(body, "description", MAX_TEXT["description"]) or ""
    category = optional_text(body, "category", MAX_TEXT["category"]) or None
    latitude = parse_coordinate(body, "latitude", -90, 90)
    longitude = parse_coordinate(body, "longitude", -180, 180)
    if (latitude is None) != (longitude is None):
        raise RequestError(400, "latitude and longitude must be provided together")
    urgency = body.get("citizenUrgency") if body.get("citizenUrgency") in ("LOW", "MEDIUM", "HIGH") else None
    exclude_id = body.get("excludeId") if isinstance(body.get("excludeId"), str) else None
    ref = load_reference()
    suggestion = intelligence.suggest_category(title, description)
    effective_category = category or suggestion["category"] or "Other"
    t = triage(ref, get_table(), effective_category, title, description, latitude, longitude, urgency, exclude_id=exclude_id)
    duplicates = []
    for distance, item in t["nearby"][:10]:
        entry = {"distanceMeters": distance, "status": lc.effective_status(item), "category": item.get("category"),
                 "createdAt": item.get("createdAt"), "yours": item.get("citizenSub") == user["sub"]}
        if user["role"] != "Citizen" or entry["yours"]:
            entry.update({"complaintId": item["complaintId"], "title": item.get("title")})
        duplicates.append(entry)
    return response(200, {
        "suggestedCategory": suggestion,
        "department": {"id": t["department"]["id"], "name": t["department"]["name"]},
        "priority": t["priority"] if user["role"] != "Citizen" else {"priority": t["priority"]["priority"]},
        "ward": {"id": t["ward"]["id"], "name": t["ward"]["name"]} if t["ward"] else None,
        "zone": {"id": t["zone"]["id"], "name": t["zone"]["name"]} if t["zone"] else None,
        "duplicates": duplicates,
        "summary": t["summary"],
        "method": "rule-based keyword and distance matching (no AI service)",
    })


def _encode_token(key):
    return base64.urlsafe_b64encode(json.dumps(key, default=_json_default).encode()).decode()


def _decode_token(token):
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()).decode())
        if not isinstance(key, dict) or not all(isinstance(v, str) for v in key.values()):
            raise ValueError("bad token")
        return key
    except Exception:
        raise RequestError(400, "Invalid nextToken")


def get_complaints(user, params=None):
    """List complaints visible to the caller. Optional ?limit=1..200&nextToken=...
    pagination; without limit every visible complaint is returned (backward compatible)."""
    params = params or {}
    table = get_table()
    limit = params.get("limit")
    if limit is not None:
        if not str(limit).isdigit() or not 1 <= int(limit) <= 200:
            raise RequestError(400, "limit must be between 1 and 200")
        limit = int(limit)
    token = _decode_token(params["nextToken"]) if params.get("nextToken") else None
    next_token = None

    if user["role"] == "Citizen":
        items = query_citizen_complaints(table, user["sub"])
        items.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
        if limit:
            offset = int(token.get("offset", "0")) if token and token.get("offset", "").isdigit() else 0
            next_token = _encode_token({"offset": str(offset + limit)}) if offset + limit < len(items) else None
            items = items[offset: offset + limit]
    elif user["role"] in ("Officer", "Admin"):
        scan_arguments = {"ConsistentRead": True}
        if user["role"] == "Officer":
            scan_arguments["FilterExpression"] = Attr("assignedOfficerSub").eq(user["sub"])
        if limit:
            scan_arguments["Limit"] = limit
            if token:
                scan_arguments["ExclusiveStartKey"] = token
            result = table.scan(**scan_arguments)
            items = result.get("Items", [])
            next_token = _encode_token(result["LastEvaluatedKey"]) if result.get("LastEvaluatedKey") else None
        else:
            items = scan_all(table, **scan_arguments)
    else:
        raise RequestError(403, "Insufficient permissions")

    now = utcnow()
    settings = load_reference()["settings"]
    complaints = [serialize_complaint(item, user, now=now, settings=settings) for item in items if can_view(item, user)]
    complaints.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
    log("LIST COMPLAINTS", role=user["role"], count=len(complaints))
    return response(200, {"count": len(complaints), "complaints": complaints, "serverTime": now.isoformat(), "nextToken": next_token})


def get_complaint(user, complaint_id):
    validate_complaint_id(complaint_id)
    complaint = load_complaint(complaint_id)
    require_complaint_access(complaint, user)
    return response(200, {"complaint": serialize_complaint(complaint, user, settings=load_reference()["settings"])})


# --------------------------------------------------
# ADMIN ACTIONS
# --------------------------------------------------

def update_complaint_metadata(event, user, complaint_id):
    """Admin: priority, department, zone and ward (managed IDs or legacy free text)."""
    require_admin(user)
    validate_complaint_id(complaint_id)
    body = parse_body(event)
    ref = load_reference()

    updates = {}
    if "priority" in body:
        priority = lc.normalize_priority(body["priority"])
        if not priority:
            raise RequestError(400, f"priority must be one of {', '.join(lc.PRIORITIES)}")
        updates["priority"] = priority
    if "departmentId" in body or "department" in body:
        dept = (reference.department_by_id(ref, body["departmentId"]) if "departmentId" in body
                else reference.department_by_name(ref, optional_text(body, "department", MAX_AREA)))
        if not dept:
            raise RequestError(400, "Unknown department")
        updates.update({"departmentId": dept["id"], "department": dept["name"]})
    for level, collection in (("ward", ref["wards"]), ("zone", ref["zones"])):
        id_field = f"{level}Id"
        if id_field in body:
            if body[id_field] in ("", None):
                updates.update({id_field: "", level: ""})
                continue
            area = next((a for a in collection if a["id"] == body[id_field]), None)
            if not area:
                raise RequestError(400, f"Unknown {level}")
            updates.update({id_field: area["id"], level: area["name"]})
            if level == "ward" and area.get("zoneId") and "zoneId" not in body:
                zone = next((z for z in ref["zones"] if z["id"] == area["zoneId"]), None)
                if zone:
                    updates.update({"zoneId": zone["id"], "zone": zone["name"]})
        elif level in body:
            updates[level] = optional_text(body, level, MAX_AREA) or ""
    if not updates:
        raise RequestError(400, "No supported fields to update")

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    status = lc.effective_status(complaint)
    changes = {k: v for k, v in updates.items() if complaint.get(k) != v}
    if not changes:
        return response(200, {"message": "No changes", "complaint": serialize_complaint(complaint, user)})
    changes["updatedAt"] = utcnow().isoformat()
    summary = ", ".join(f"{k}={v or '-'}" for k, v in changes.items() if k not in ("updatedAt", "departmentId", "zoneId", "wardId"))
    updated = apply_transition(table, complaint, changes, history_entry("details_updated", user, status, status, note=summary))
    audit(user, "complaint_details_updated", complaint_id, {k: v for k, v in changes.items() if k != "updatedAt"})
    return response(200, {"message": "Complaint updated", "complaint": serialize_complaint(updated, user)})


def resolve_officer(officer_sub):
    """Verified officer identity: an enabled member of the Cognito Officer group."""
    if LOCAL_DEMO_AUTH_ENABLED:
        demo = next((o for o in DEMO_OFFICERS if o["sub"] == officer_sub), None)
        if not demo:
            raise RequestError(400, "Unknown local demo officer")
        return demo
    if not USER_POOL_ID:
        raise RequestError(500, "Officer directory is not configured")
    officer = find_officer(officer_sub)
    if not officer or not officer.get("enabled", True):
        raise RequestError(400, "Selected user is not an active member of the CivicTrack Officer group")
    return officer


def assign_complaint(event, user, complaint_id):
    """Assign a PENDING/REOPENED complaint, or reassign one that has not started."""
    require_admin(user)
    validate_complaint_id(complaint_id)
    body = parse_body(event)

    officer_sub = body.get("officerSub")
    if not isinstance(officer_sub, str) or not officer_sub.strip() or len(officer_sub.strip()) > 128:
        raise RequestError(400, "Officer ID is required")
    officer_sub = officer_sub.strip()
    officer = resolve_officer(officer_sub)
    officer_name = officer["name"] or officer["email"] or "Officer"

    profile = officer_profile(load_reference(), officer_sub)
    if not profile["available"] and body.get("force") is not True:
        raise RequestError(409, f"{officer_name} is marked unavailable. Choose another officer or confirm the override.")

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    current = lc.effective_status(complaint)
    if current == lc.IN_PROGRESS:
        raise RequestError(409, "Work has started; the complaint can no longer be reassigned")
    if current == lc.ASSIGNED and complaint.get("assignedOfficerSub") == officer_sub:
        raise RequestError(409, "Complaint is already assigned to this officer")
    action = "reassign" if current == lc.ASSIGNED else "assign"
    require_transition(action, complaint)

    reason = optional_text(body, "reason", MAX_NOTE)
    previous_sub = complaint.get("assignedOfficerSub") or ""
    previous_name = complaint.get("assignedOfficerName") or ""
    now = utcnow().isoformat()
    updated = apply_transition(
        table,
        complaint,
        {"status": lc.ASSIGNED, "assignedOfficerSub": officer_sub, "assignedOfficerName": officer_name,
         "assignedAt": now, "assignedBySub": user["sub"]},
        history_entry("reassigned" if action == "reassign" else "assigned", user, current, lc.ASSIGNED,
                      note=reason, officerName=officer_name,
                      previousOfficerName=previous_name if previous_sub and previous_sub != officer_sub else None),
        remove_fields=("acceptedAt",),
    )
    log("ASSIGN", complaintId=complaint_id, action=action)
    audit(user, f"complaint_{action}ed", complaint_id, {"officerSub": officer_sub, "override": body.get("force") is True})
    notify_complaint_assigned(updated)
    if previous_sub and previous_sub != officer_sub:
        notify_officer_removed(updated, previous_sub, "reassigned to another officer")
    return response(200, {"message": "Complaint reassigned successfully" if action == "reassign" else "Complaint assigned successfully",
                          "complaint": serialize_complaint(updated, user)})


def unassign_complaint(event, user, complaint_id):
    require_admin(user)
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=False)
    reason = optional_text(body, "reason", MAX_NOTE)
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    current = require_transition("unassign", complaint)
    previous_sub = complaint.get("assignedOfficerSub")
    updated = apply_transition(
        table,
        complaint,
        {"status": lc.PENDING, "assignedOfficerSub": "", "assignedOfficerName": "", "unassignedAt": utcnow().isoformat()},
        history_entry("unassigned", user, current, lc.PENDING, note=reason, previousOfficerName=complaint.get("assignedOfficerName")),
        remove_fields=("acceptedAt",),
    )
    audit(user, "complaint_unassigned", complaint_id, {"previousOfficerSub": previous_sub, "reason": reason})
    if previous_sub:
        notify_officer_removed(updated, previous_sub, "unassigned")
    return response(200, {"message": "Officer unassigned", "complaint": serialize_complaint(updated, user)})


def reject_complaint(event, user, complaint_id):
    """Admin closes an invalid, duplicate or out-of-scope complaint without work."""
    require_admin(user)
    validate_complaint_id(complaint_id)
    body = parse_body(event)
    reason = optional_text(body, "reason", MAX_NOTE)
    if not reason or len(reason) < 10:
        raise RequestError(400, "A reason of at least 10 characters is required; it is shown to the citizen")
    duplicate_of = body.get("duplicateOf")
    if duplicate_of is not None:
        validate_complaint_id(duplicate_of)
        if duplicate_of == complaint_id:
            raise RequestError(400, "A complaint cannot duplicate itself")
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    if duplicate_of:
        load_complaint(duplicate_of, table)
    current = require_transition("reject", complaint)
    fields = {"status": lc.REJECTED, "rejectedAt": utcnow().isoformat(), "rejectionReason": reason}
    if duplicate_of:
        fields["duplicateOf"] = duplicate_of
    previous_sub = complaint.get("assignedOfficerSub")
    updated = apply_transition(table, complaint, fields, history_entry("rejected", user, current, lc.REJECTED, note=reason))
    audit(user, "complaint_rejected", complaint_id, {"reason": reason, "duplicateOf": duplicate_of})
    notify_complaint_rejected(updated, reason)
    if previous_sub:
        notify_officer_removed(updated, previous_sub, "closed by the administrator")
    return response(200, {"message": "Complaint rejected", "complaint": serialize_complaint(updated, user)})


def escalate_complaint(event, user, complaint_id):
    """Manual escalation: raises priority one level and notifies officer and admins."""
    require_admin(user)
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=False)
    reason = optional_text(body, "reason", MAX_NOTE)
    if not reason or len(reason) < 5:
        raise RequestError(400, "An escalation reason of at least 5 characters is required")
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    current = require_transition("escalate", complaint)
    level = int(complaint.get("escalationLevel", 1 if complaint.get("escalated") else 0) or 0) + 1
    priority = lc.normalize_priority(complaint.get("priority")) or "MEDIUM"
    raised = lc.PRIORITIES[min(len(lc.PRIORITIES) - 1, lc.PRIORITIES.index(priority) + 1)]
    now = utcnow().isoformat()
    updated = apply_transition(table, complaint,
                               {"escalated": True, "escalatedAt": now, "escalationLevel": level, "priority": raised},
                               history_entry("escalated", user, current, current, note=reason, level=level, priority=raised))
    audit(user, "complaint_escalated", complaint_id, {"level": level, "reason": reason})
    notify_escalated(updated, level)
    return response(200, {"message": f"Escalated to level {level}", "complaint": serialize_complaint(updated, user)})


# --------------------------------------------------
# OFFICER WORKFLOW
# --------------------------------------------------

MAX_PROGRESS_PHOTOS = 10


def update_officer_workflow(event, user, complaint_id, action):
    """Only the assigned officer can accept, decline, start, update or resolve."""
    if user["role"] != "Officer":
        raise RequestError(403, "Only officers can update complaint progress")
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=action in ("resolve", "decline", "progress"))
    note = optional_text(body, "note", MAX_NOTE)

    table = get_table()
    complaint = load_complaint(complaint_id, table)
    if complaint.get("assignedOfficerSub") != user["sub"]:
        raise RequestError(403, "Complaint is not assigned to you")
    current = require_transition(action, complaint)
    now = utcnow().isoformat()

    if action == "accept":
        if complaint.get("acceptedAt"):
            raise RequestError(409, "You have already accepted this assignment")
        updated = apply_transition(table, complaint, {"acceptedAt": now}, history_entry("accepted", user, current, current, note=note))
        notify_assignment_response(updated, accepted=True)
        return response(200, {"message": "Assignment accepted", "complaint": serialize_complaint(updated, user)})

    if action == "decline":
        reason = optional_text(body, "reason", MAX_NOTE) or note
        if not reason or len(reason) < 10:
            raise RequestError(400, "A reason of at least 10 characters is required to decline an assignment")
        updated = apply_transition(
            table, complaint,
            {"status": lc.PENDING, "assignedOfficerSub": "", "assignedOfficerName": "", "declinedAt": now},
            history_entry("assignment_declined", user, current, lc.PENDING, note=reason, previousOfficerName=complaint.get("assignedOfficerName")),
            remove_fields=("acceptedAt",),
        )
        notify_assignment_response(updated, accepted=False, reason=reason)
        return response(200, {"message": "Assignment declined; returned to the administrator", "complaint": serialize_complaint(updated, user)})

    if action == "start":
        fields = {"status": lc.IN_PROGRESS, "workStartedAt": now}
        if not complaint.get("acceptedAt"):
            fields["acceptedAt"] = now  # starting work implies acceptance
        before_key = body.get("beforePhotoKey")
        if before_key is not None:
            valid, message = validate_uploaded_image(before_key, officer_media_prefix(user["sub"], complaint_id, "before"))
            if not valid:
                raise RequestError(400, message)
            fields["beforePhotoKey"] = before_key
        updated = apply_transition(table, complaint, fields, history_entry("work_started", user, current, lc.IN_PROGRESS, note=note))
        notify_work_started(updated)
        return response(200, {"message": "Work started successfully", "complaint": serialize_complaint(updated, user)})

    if action == "progress":
        if not note or len(note) < 3:
            raise RequestError(400, "A progress note is required")
        fields = {"lastProgressAt": now}
        photo_key = body.get("photoKey")
        if photo_key is not None:
            valid, message = validate_uploaded_image(photo_key, officer_media_prefix(user["sub"], complaint_id, "progress"))
            if not valid:
                raise RequestError(400, message)
            keys = list(complaint.get("progressPhotoKeys", []))
            if len(keys) >= MAX_PROGRESS_PHOTOS:
                raise RequestError(409, f"At most {MAX_PROGRESS_PHOTOS} progress photos per complaint")
            fields["progressPhotoKeys"] = keys + [photo_key]
        percent = body.get("percentComplete")
        if percent is not None:
            if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
                raise RequestError(400, "percentComplete must be an integer from 0 to 100")
            fields["percentComplete"] = percent
        updated = apply_transition(table, complaint, fields,
                                   history_entry("progress_update", user, current, current, note=note,
                                                 percentComplete=percent, hasPhoto=bool(photo_key) or None))
        return response(200, {"message": "Progress recorded", "complaint": serialize_complaint(updated, user)})

    proof_key = body.get("proofPhotoKey")
    if not isinstance(proof_key, str) or not proof_key.strip():
        raise RequestError(400, "A valid proofPhotoKey is required")
    proof_key = proof_key.strip()
    if not validate_object_key(proof_key):
        raise RequestError(400, "Invalid proofPhotoKey")
    valid, message = validate_uploaded_image(proof_key, officer_media_prefix(user["sub"], complaint_id, "after"))
    if not valid:
        raise RequestError(400, message)
    fields = {"status": lc.RESOLVED, "resolvedAt": now, "resolvedBySub": user["sub"], "proofPhotoKey": proof_key, "percentComplete": 100}
    if note:
        fields["resolutionNote"] = note
    updated = apply_transition(table, complaint, fields, history_entry("resolved", user, current, lc.RESOLVED, note=note))
    notify_complaint_resolved(updated)
    return response(200, {"message": "Complaint resolved successfully", "complaint": serialize_complaint(updated, user)})


# --------------------------------------------------
# CITIZEN FEEDBACK: REOPEN / CLOSE
# --------------------------------------------------

REOPEN_WINDOW_DAYS = 14
MAX_REOPENS = 3


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
    resolved_at = lc.parse_iso_datetime(complaint.get("resolvedAt"))
    if resolved_at and utcnow() - resolved_at > timedelta(days=REOPEN_WINDOW_DAYS):
        raise RequestError(409, f"Complaints can only be reopened within {REOPEN_WINDOW_DAYS} days of resolution. Please submit a new complaint.")
    if int(complaint.get("reopenCount", 0) or 0) >= MAX_REOPENS:
        raise RequestError(409, f"This complaint has already been reopened {MAX_REOPENS} times. Please submit a new complaint.")
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
            "escalationLevel": 0,
        },
        history_entry("reopened", user, current, lc.REOPENED, note=reason),
        remove_fields=("slaWarningSentAt", "escalationNotifiedAt", "escalationLevel2NotifiedAt", "escalatedAt", "acceptedAt"),
    )
    notify_complaint_reopened(updated)
    return response(200, {"message": "Complaint reopened", "complaint": serialize_complaint(updated, user)})


def close_complaint(event, user, complaint_id):
    if user["role"] not in ("Citizen", "Admin"):
        raise RequestError(403, "Only the reporting citizen or an Admin can close a complaint")
    validate_complaint_id(complaint_id)
    body = parse_body(event, required=False)
    note = optional_text(body, "note", MAX_NOTE)
    feedback = optional_text(body, "feedback", MAX_NOTE)
    rating = body.get("rating")
    if rating is not None and (not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5):
        raise RequestError(400, "rating must be an integer from 1 to 5")
    table = get_table()
    complaint = load_complaint(complaint_id, table)
    require_complaint_access(complaint, user)
    current = require_transition("close", complaint)
    fields = {"status": lc.CLOSED, "closedAt": utcnow().isoformat(), "closedByRole": user["role"]}
    if user["role"] == "Citizen":
        if rating is not None:
            fields["citizenRating"] = rating
        if feedback:
            fields["citizenFeedback"] = feedback
    updated = apply_transition(table, complaint, fields, history_entry("closed", user, current, lc.CLOSED, note=note or feedback, rating=rating))
    if user["role"] == "Admin":
        audit(user, "complaint_closed", complaint_id, {"note": note})
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


SECOND_LEVEL_AFTER_HOURS = 24


def process_sla_checks(now=None):
    """SLA sweep, safe under EventBridge duplicate delivery, Lambda retries and
    concurrent executions (claim-then-send). One bad record never aborts it.

    For each open complaint, at most one notification per run, in order:
      1. warning      - final warningHours before the deadline (once)
      2. breach       - deadline passed: escalated=True + officer/admin email (once)
      3. level 2      - still open 24 h after the deadline: admin email (once)
    """
    table = get_table()
    now = now or utcnow()
    stamp = now.isoformat()
    warning_hours = max(1, int(load_reference()["settings"].get("warningHours", SLA_WARNING_HOURS)))
    scan_args = {
        "FilterExpression": Attr("status").is_in(SLA_SWEEP_STATUSES),
        "ProjectionExpression": (
            "complaintId, #status, title, category, #loc, citizenEmail, assignedOfficerSub, assignedOfficerName, "
            "createdAt, slaStartedAt, slaHours, escalated, slaWarningSentAt, escalationNotifiedAt, escalationLevel2NotifiedAt"
        ),
        "ExpressionAttributeNames": {"#status": "status", "#loc": "location"},
    }
    summary = {"status": "completed", "scanned": 0, "warningsSent": 0, "newEscalations": 0, "secondLevelEscalations": 0,
               "notificationEmailsSent": 0, "malformedTimestamps": 0, "errors": 0, "checkedAt": stamp, "warningHours": warning_hours}
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
                warning_at = deadline - timedelta(hours=min(warning_hours, hours))

                if now >= deadline:
                    if not complaint.get("escalated") and mark_escalated(table, complaint_id, stamp):
                        summary["newEscalations"] += 1
                    if not complaint.get("escalationNotifiedAt"):
                        flag, subject, text, staff = (
                            "escalationNotifiedAt", f"CivicTrack SLA BREACHED: {complaint_id}",
                            f"The {hours}-hour SLA has been breached. This complaint has been escalated and requires immediate attention.", True)
                    elif now >= deadline + timedelta(hours=SECOND_LEVEL_AFTER_HOURS) and not complaint.get("escalationLevel2NotifiedAt"):
                        flag, subject, text, staff = (
                            "escalationLevel2NotifiedAt", f"CivicTrack ESCALATION level 2: {complaint_id}",
                            f"This complaint is still unresolved {SECOND_LEVEL_AFTER_HOURS} hours after its SLA deadline. Senior review is required.", False)
                    else:
                        continue
                elif now >= warning_at and not complaint.get("slaWarningSentAt"):
                    flag, subject, text, staff = (
                        "slaWarningSentAt", f"CivicTrack SLA warning: {complaint_id}",
                        f"This complaint is approaching its {hours}-hour SLA deadline. Please take action before {deadline.isoformat()}.", True)
                else:
                    continue

                if not claim_flag(table, complaint_id, flag, stamp):
                    continue
                if admins is None:
                    admins = admin_recipients()
                recipients = ([get_officer_email(complaint.get("assignedOfficerSub", ""), officer_cache)] if staff else []) + admins
                if send_notification(recipients, subject, complaint_email_body(complaint, text)):
                    summary["notificationEmailsSent"] += 1
                    if flag == "slaWarningSentAt":
                        summary["warningsSent"] += 1
                    if flag == "escalationLevel2NotifiedAt":
                        summary["secondLevelEscalations"] += 1
                        append_system_history(table, complaint_id, "sla_escalated_level2", stamp)
                else:
                    release_flag(table, complaint_id, flag, stamp)
            except Exception as error:
                summary["errors"] += 1
                log("SLA CHECK ERROR", level="ERROR", complaintId=complaint_id, errorType=type(error).__name__)
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

    log("SLA CHECK SUMMARY", **summary)
    return summary


def append_system_history(table, complaint_id, action, stamp, note=None):
    entry = {"at": stamp, "action": action, "actorRole": "System", "actorSub": "sla-checker", "fromStatus": None, "toStatus": None}
    if note:
        entry["note"] = note
    table.update_item(
        Key={"complaintId": complaint_id},
        UpdateExpression="SET #history = list_append(if_not_exists(#history, :emptyList), :entry), escalationLevel = :two",
        ConditionExpression="attribute_exists(complaintId)",
        ExpressionAttributeNames={"#history": "history"},
        ExpressionAttributeValues={":entry": [entry], ":emptyList": [], ":two": 2},
    )


# --------------------------------------------------
# REFERENCE DATA & CONFIGURATION (departments, categories, zones, wards, settings)
# --------------------------------------------------

NAME_PATTERN = re.compile(r"[^\x00-\x1f<>]{1,80}")


def _valid_id(value, label):
    if not isinstance(value, str) or not reference.ID_PATTERN.fullmatch(value):
        raise RequestError(400, f"Invalid {label} ID (lowercase letters, digits and hyphens, max 48)")
    return value


def _name(body, field="name", required=True, max_length=80):
    value = optional_text(body, field, max_length)
    if required and not value:
        raise RequestError(400, f"{field} is required")
    if value and not NAME_PATTERN.fullmatch(value):
        raise RequestError(400, f"{field} contains invalid characters")
    return value


def _active(body, default=True):
    value = body.get("active", default)
    if not isinstance(value, bool):
        raise RequestError(400, "active must be true or false")
    return value


def materialize_builtin_departments(table):
    """First admin edit: persist the built-in departments so none disappear."""
    if not reference._query_all(table, "DEPT"):
        for dept in reference.builtin_departments():
            table.put_item(Item={"pk": "DEPT", "sk": dept["id"], "name": dept["name"], "description": "", "active": True,
                                 "createdAt": utcnow().isoformat()})
        reference.invalidate()


def get_reference(event, user):
    params = event.get("queryStringParameters") or {}
    ref = load_reference()
    return response(200, reference.public_view(ref, include_boundaries=params.get("boundaries") in ("1", "true")))


def initialize_reference(event, user):
    require_admin(user)
    table = get_config_table()
    materialize_builtin_departments(table)
    ref = reference.load(table, use_cache=False)
    for category in reference.builtin_categories():
        if not any(c["name"] == category["name"] and not c.get("builtin") for c in ref["categories"]):
            table.put_item(Item={"pk": "CATEGORY", "sk": category["name"], "departmentId": category["departmentId"], "active": True})
    reference.invalidate()
    audit(user, "reference_initialized", "config")
    return response(200, reference.public_view(reference.load(table, use_cache=False)))


def put_department(event, user, dept_id):
    require_admin(user)
    _valid_id(dept_id, "department")
    body = parse_body(event)
    table = get_config_table()
    materialize_builtin_departments(table)
    item = {"name": _name(body), "description": optional_text(body, "description", 300) or "", "active": _active(body),
            "updatedAt": utcnow().isoformat()}
    if any(d["name"].lower() == item["name"].lower() and d["id"] != dept_id for d in reference.load(table, use_cache=False)["departments"]):
        raise RequestError(409, "Another department already uses this name")
    reference.put_item(table, "DEPT", dept_id, item)
    audit(user, "department_saved", dept_id, item)
    return response(200, {"department": {"id": dept_id, **item}})


def put_category(event, user, category_name):
    require_admin(user)
    if not NAME_PATTERN.fullmatch(category_name or "") or len(category_name) > MAX_TEXT["category"]:
        raise RequestError(400, "Invalid category name")
    body = parse_body(event)
    table = get_config_table()
    ref = reference.load(table, use_cache=False)
    dept_id = body.get("departmentId")
    if not reference.department_by_id(ref, dept_id):
        raise RequestError(400, "Unknown department")
    materialize_builtin_departments(table)
    item = {"departmentId": dept_id, "active": _active(body), "updatedAt": utcnow().isoformat()}
    reference.put_item(table, "CATEGORY", category_name, item)
    audit(user, "category_saved", category_name, item)
    return response(200, {"category": {"name": category_name, **item}})


def _boundary_fields(body):
    if body.get("boundary") in (None, ""):
        return {}
    try:
        geometry, bbox, vertices = geo.validate_geometry(body["boundary"])
    except geo.GeometryError as error:
        raise RequestError(400, str(error))
    return {**reference.boundary_attributes(geometry, bbox), "boundaryVertices": vertices,
            "boundarySource": optional_text(body, "boundarySource", 200) or ""}


def put_zone(event, user, zone_id):
    require_admin(user)
    _valid_id(zone_id, "zone")
    body = parse_body(event)
    item = {"name": _name(body), "active": _active(body), "updatedAt": utcnow().isoformat(), **_boundary_fields(body)}
    table = get_config_table()
    if not item.get("boundaryJson"):
        existing = table.get_item(Key={"pk": "ZONE", "sk": zone_id}).get("Item") or {}
        item.update({k: existing[k] for k in ("boundaryJson", "bboxJson", "boundaryVertices", "boundarySource") if k in existing})
    reference.put_item(table, "ZONE", zone_id, item)
    audit(user, "zone_saved", zone_id, {"name": item["name"], "active": item["active"], "hasBoundary": bool(item.get("boundaryJson"))})
    return response(200, {"zone": {"id": zone_id, "name": item["name"], "active": item["active"], "hasBoundary": bool(item.get("boundaryJson"))}})


def put_ward(event, user, ward_id):
    require_admin(user)
    _valid_id(ward_id, "ward")
    body = parse_body(event)
    table = get_config_table()
    ref = reference.load(table, use_cache=False)
    zone_id = body.get("zoneId") or ""
    if zone_id and not any(z["id"] == zone_id for z in ref["zones"]):
        raise RequestError(400, "Unknown zone")
    number = optional_text(body, "number", 10) or ""
    item = {"name": _name(body), "number": number, "zoneId": zone_id, "active": _active(body),
            "updatedAt": utcnow().isoformat(), **_boundary_fields(body)}
    if not item.get("boundaryJson"):
        existing = table.get_item(Key={"pk": "WARD", "sk": ward_id}).get("Item") or {}
        item.update({k: existing[k] for k in ("boundaryJson", "bboxJson", "boundaryVertices", "boundarySource") if k in existing})
    reference.put_item(table, "WARD", ward_id, item)
    audit(user, "ward_saved", ward_id, {"name": item["name"], "zoneId": zone_id, "hasBoundary": bool(item.get("boundaryJson"))})
    return response(200, {"ward": {"id": ward_id, "name": item["name"], "number": number, "zoneId": zone_id,
                                   "active": item["active"], "hasBoundary": bool(item.get("boundaryJson"))}})


MAX_IMPORT_FEATURES = 500


def import_boundaries(event, user):
    """Import zones or wards from a GeoJSON FeatureCollection supplied by the admin."""
    require_admin(user)
    body = parse_body(event)
    level = body.get("level")
    if level not in ("zone", "ward"):
        raise RequestError(400, "level must be 'zone' or 'ward'")
    collection = body.get("featureCollection")
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
        raise RequestError(400, "featureCollection must be a GeoJSON FeatureCollection")
    features = collection["features"]
    if not features or len(features) > MAX_IMPORT_FEATURES:
        raise RequestError(400, f"Import between 1 and {MAX_IMPORT_FEATURES} features at a time")
    name_prop = body.get("nameProperty") or "name"
    id_prop = body.get("idProperty")
    number_prop = body.get("numberProperty")
    zone_prop = body.get("zoneProperty")
    source = optional_text(body, "source", 200) or ""
    table = get_config_table()
    ref = reference.load(table, use_cache=False)
    zones_by_key = {z["id"]: z for z in ref["zones"]} | {z["name"].lower(): z for z in ref["zones"]}
    imported, skipped = [], []
    for index, feature in enumerate(features):
        props = (feature or {}).get("properties") or {}
        name = str(props.get(name_prop) or "").strip()[:80]
        if not name:
            skipped.append({"index": index, "reason": f"missing '{name_prop}' property"})
            continue
        try:
            geometry, bbox, vertices = geo.validate_geometry((feature or {}).get("geometry"))
        except geo.GeometryError as error:
            skipped.append({"index": index, "name": name, "reason": str(error)})
            continue
        area_id = reference.slugify(str(props.get(id_prop)) if id_prop and props.get(id_prop) is not None else name)
        item = {"name": name, "active": True, "updatedAt": utcnow().isoformat(), "boundaryVertices": vertices,
                "boundarySource": source, **reference.boundary_attributes(geometry, bbox)}
        if level == "ward":
            item["number"] = str(props.get(number_prop) or "")[:10] if number_prop else ""
            zone_key = str(props.get(zone_prop) or "").strip() if zone_prop else ""
            zone = zones_by_key.get(reference.slugify(zone_key)) or zones_by_key.get(zone_key.lower()) if zone_key else None
            item["zoneId"] = zone["id"] if zone else ""
        table.put_item(Item={"pk": level.upper(), "sk": area_id, **item})
        imported.append({"id": area_id, "name": name, "vertices": vertices})
    reference.invalidate()
    audit(user, f"{level}_boundaries_imported", level, {"imported": len(imported), "skipped": len(skipped), "source": source})
    return response(200, {"imported": imported, "skipped": skipped})


def put_settings(event, user):
    require_admin(user)
    body = parse_body(event)
    settings = dict(load_reference()["settings"])
    rules = {"slaHours": (1, 720), "warningHours": (1, 168), "maxComplaintsPerHour": (1, 100)}
    for key, (low, high) in rules.items():
        if key in body:
            value = body[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise RequestError(400, f"{key} must be an integer from {low} to {high}")
            settings[key] = value
    if "discloseOfficerName" in body:
        if not isinstance(body["discloseOfficerName"], bool):
            raise RequestError(400, "discloseOfficerName must be true or false")
        settings["discloseOfficerName"] = body["discloseOfficerName"]
    if settings["warningHours"] >= settings["slaHours"]:
        raise RequestError(400, "warningHours must be less than slaHours")
    reference.put_item(get_config_table(), "SETTINGS", "global", {**settings, "updatedAt": utcnow().isoformat()})
    audit(user, "settings_updated", "global", settings)
    return response(200, {"settings": settings, "note": "slaHours applies to complaints created after this change"})


def get_audit_log(event, user):
    require_admin(user)
    params = event.get("queryStringParameters") or {}
    month = params.get("month") or utcnow().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise RequestError(400, "month must be YYYY-MM")
    return response(200, {"month": month, "entries": reference.read_audit(get_config_table(), month)})


# --------------------------------------------------
# ANALYTICS, NOTIFICATION CENTRE, PROFILE
# --------------------------------------------------

ANALYTICS_FIELDS = ("complaintId", "status", "category", "department", "departmentId", "zone", "ward", "priority", "createdAt",
                    "resolvedAt", "closedAt", "slaStartedAt", "slaHours", "escalated", "reopenCount", "assignedOfficerName",
                    "assignedOfficerSub", "latitude", "longitude")


def projection(fields):
    """Alias every attribute so DynamoDB reserved words (zone, status, ...) are safe."""
    names = {f"#p{i}": field for i, field in enumerate(fields)}
    return {"ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}


def get_analytics(event, user):
    """Server-side aggregation over every stored complaint (admin only)."""
    require_admin(user)
    params = event.get("queryStringParameters") or {}
    filters = {}
    for key in ("from", "to"):
        if params.get(key):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", params[key]):
                raise RequestError(400, f"{key} must be YYYY-MM-DD")
            filters[key] = params[key]
    for key in ("department", "zone", "ward", "category", "priority"):
        if params.get(key):
            filters[key] = params[key][:80]
    granularity = params.get("granularity", "day")
    if granularity not in ("day", "week", "month"):
        raise RequestError(400, "granularity must be day, week or month")
    items = scan_all(get_table(), **projection(ANALYTICS_FIELDS))
    warning_hours = int(load_reference()["settings"].get("warningHours", SLA_WARNING_HOURS))
    return response(200, analytics_engine.aggregate(items, utcnow(), filters, granularity, warning_hours))


NOTIFY_ACTIONS = {
    "Citizen": {"assigned", "accepted", "work_started", "progress_update", "resolved", "rejected", "closed", "escalated"},
    "Officer": {"assigned", "reassigned", "reopened", "escalated", "sla_escalated", "sla_escalated_level2", "details_updated", "closed"},
    "Admin": {"created", "reopened", "assignment_declined", "sla_escalated", "sla_escalated_level2", "closed"},
}


def get_notifications(event, user):
    """Notification centre derived from persisted complaint history (last 30 days)."""
    table = get_table()
    if user["role"] == "Citizen":
        items = query_citizen_complaints(table, user["sub"])
    elif user["role"] == "Officer":
        items = scan_all(table, FilterExpression=Attr("assignedOfficerSub").eq(user["sub"]))
    else:
        items = scan_all(table)
    since = (utcnow() - timedelta(days=30)).isoformat()
    wanted = NOTIFY_ACTIONS[user["role"]]
    settings = load_reference()["settings"]
    notifications = []
    for item in items:
        if not can_view(item, user):
            continue
        for entry in item.get("history", []):
            if entry.get("at", "") < since or entry.get("action") not in wanted or entry.get("actorSub") == user["sub"]:
                continue
            note = entry.get("note") if entry.get("action") in ("rejected", "reopened", "assignment_declined", "progress_update", "escalated") else None
            record = {"complaintId": item["complaintId"], "title": item.get("title"), "action": entry["action"],
                      "at": entry["at"], "actorRole": entry.get("actorRole"), "status": lc.effective_status(item)}
            if note:
                record["note"] = note
            if user["role"] != "Citizen" or settings.get("discloseOfficerName", True):
                if entry.get("officerName"):
                    record["officerName"] = entry["officerName"]
            notifications.append(record)
    notifications.sort(key=lambda n: n["at"], reverse=True)
    return response(200, {"notifications": notifications[:100], "since": since})


def get_me(event, user):
    payload = {"sub": user["sub"], "email": user["email"], "role": user["role"], "name": user.get("name", "")}
    if user["role"] == "Officer":
        ref = load_reference()
        profile = officer_profile(ref, user["sub"])
        dept = reference.department_by_id(ref, profile["departmentId"]) if profile["departmentId"] else None
        payload["profile"] = {**profile, "departmentName": dept["name"] if dept else "",
                              "zoneNames": [z["name"] for z in ref["zones"] if z["id"] in profile["zoneIds"]],
                              "wardNames": [w["name"] for w in ref["wards"] if w["id"] in profile["wardIds"]]}
    return response(200, {"user": payload})


# --------------------------------------------------
# REQUEST ROUTER
# --------------------------------------------------

ID = r"([^/]+)"
ROUTES = [
    # (pattern, {method: handler(event, user, *groups)}) — first matching pattern wins.
    (r"/evidence/upload-url", {"POST": lambda e, u: create_citizen_upload_url(e, u)}),
    (r"/me", {"GET": get_me}),
    (r"/reference", {"GET": get_reference}),
    (r"/notifications", {"GET": get_notifications}),
    (r"/analytics", {"GET": get_analytics}),
    (r"/officers", {"GET": lambda e, u: list_officers(u)}),
    (r"/complaints", {"GET": lambda e, u: get_complaints(u, e.get("queryStringParameters") or {}), "POST": create_complaint}),
    (r"/complaints/similar", {"POST": check_similar}),
    (rf"/complaints/{ID}", {"GET": lambda e, u, c: get_complaint(u, c), "PATCH": update_complaint_metadata}),
    (rf"/complaints/{ID}/proof-upload-url", {"POST": create_officer_proof_upload_url}),
    (rf"/complaints/{ID}/assign", {"PATCH": assign_complaint}),
    (rf"/complaints/{ID}/unassign", {"PATCH": unassign_complaint}),
    (rf"/complaints/{ID}/reject", {"PATCH": reject_complaint}),
    (rf"/complaints/{ID}/escalate", {"PATCH": escalate_complaint}),
    (rf"/complaints/{ID}/(accept|decline|start|progress|resolve)", {"PATCH": lambda e, u, c, a: update_officer_workflow(e, u, c, a)}),
    (rf"/complaints/{ID}/reopen", {"PATCH": reopen_complaint}),
    (rf"/complaints/{ID}/close", {"PATCH": close_complaint}),
    (r"/admin/users", {"GET": list_users, "POST": create_user}),
    (rf"/admin/users/{ID}", {"PATCH": update_user}),
    (rf"/admin/officers/{ID}/profile", {"PUT": update_officer_profile}),
    (r"/admin/reference/initialize", {"POST": initialize_reference}),
    (rf"/admin/departments/{ID}", {"PUT": put_department}),
    (rf"/admin/categories/{ID}", {"PUT": put_category}),
    (rf"/admin/zones/{ID}", {"PUT": put_zone}),
    (rf"/admin/wards/{ID}", {"PUT": put_ward}),
    (r"/admin/boundaries/import", {"POST": import_boundaries}),
    (r"/admin/settings", {"PUT": put_settings}),
    (r"/admin/audit", {"GET": get_audit_log}),
]
COMPILED_ROUTES = [(re.compile(pattern), handlers) for pattern, handlers in ROUTES]
MAX_BODY_BYTES = 1024 * 1024
LARGE_BODY_PATHS = {"/admin/boundaries/import", "/admin/zones", "/admin/wards"}


def lambda_handler(event, context):
    event = event or {}
    _request_context["requestId"] = getattr(context, "aws_request_id", None)
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

    body = event.get("body")
    if isinstance(body, str) and len(body) > MAX_BODY_BYTES and not any(normalized_path.startswith(p) for p in LARGE_BODY_PATHS):
        return error_response(413, "Request body too large")

    for pattern, handlers in COMPILED_ROUTES:
        match = pattern.fullmatch(normalized_path)
        if not match:
            continue
        try:
            user = authenticate_request(event)
            handler = handlers.get(method)
            if not handler:
                return error_response(405, "Method not allowed")
            args = [unquote(group) for group in match.groups()]
            return handler(event, user, *args)
        except RequestError as error:
            if error.status_code >= 500:
                log("REQUEST FAILED", level="ERROR", status=error.status_code, route=pattern.pattern)
            return error_response(error.status_code, error.message)
        except Exception as error:
            log("UNHANDLED ERROR", level="ERROR", method=method, route=pattern.pattern, errorType=type(error).__name__)
            return error_response(500, "Internal server error")

    return error_response(404, "Route not found")
