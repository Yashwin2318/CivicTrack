"""Run the real CivicTrack Lambda locally against an in-memory AWS mock.

    backend/.venv/Scripts/python backend/tools/local_dev_server.py [--seed]

* API on http://127.0.0.1:3000 (what frontend/.env.local points to).
* Every AWS SDK call (DynamoDB, S3, SES, Cognito lookups) goes to a local
  moto server on 127.0.0.1:5001. Real AWS is never contacted: the script
  aborts if the endpoint is not a loopback address.
* The frontend's local demo login sends X-Local-Demo-* headers; this server
  turns them into the same claims API Gateway's Cognito authorizer provides,
  so the Lambda runs its production authentication path.
* --seed creates a few clearly labelled sample complaints through the API.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MOTO_PORT = int(os.environ.get("CT_MOTO_PORT", "5001"))
API_PORT = int(os.environ.get("CT_API_PORT", "3000"))
MOTO_URL = f"http://127.0.0.1:{MOTO_PORT}"
FRONTEND_ORIGIN = os.environ.get("CT_FRONTEND_ORIGIN", "http://localhost:5173")

# ---- isolate from real AWS before boto3 is imported anywhere ----
for var in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN", "AWS_SAM_LOCAL"):
    os.environ.pop(var, None)
os.environ.update({
    "AWS_ENDPOINT_URL": MOTO_URL,
    "AWS_ACCESS_KEY_ID": "local-dev",
    "AWS_SECRET_ACCESS_KEY": "local-dev",
    "AWS_REGION": "ap-south-1",
    "AWS_DEFAULT_REGION": "ap-south-1",
    "COMPLAINTS_TABLE": "Local-CivicTrack-Complaints",
    "CONFIG_TABLE": "Local-CivicTrack-Config",
    "S3_BUCKET": "local-civictrack-evidence",
    "S3_INTERNAL_ENDPOINT_URL": MOTO_URL,
    "S3_PRESIGN_ENDPOINT_URL": MOTO_URL,
    "USER_POOL_ID": "ap-south-1_localdev",
    "SES_FROM_EMAIL": "noreply@civictrack.local",
    "ADMIN_NOTIFICATION_EMAIL": "admin@civictrack.local",
    "ALLOWED_ORIGIN": FRONTEND_ORIGIN,
})
if urlparse(os.environ["AWS_ENDPOINT_URL"]).hostname not in ("127.0.0.1", "localhost"):
    sys.exit("Refusing to start: AWS endpoint is not local.")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "complaints"))

import boto3  # noqa: E402
from moto.server import ThreadedMotoServer  # noqa: E402

import app  # noqa: E402

# Demo logins in the frontend send fixed "local-demo-*" subjects. They are mapped
# onto real users in the local (moto) Cognito pool, so the officer directory and
# user administration run their production code paths.
DEMO_USERS = {
    "local-demo-citizen": ("demo.citizen@civictrack.local", "Demo Citizen", "Citizen"),
    "local-demo-officer": ("demo.officer@civictrack.local", "Demo Officer", "Officer"),
    "local-demo-officer-2": ("ravi.officer@civictrack.local", "Ravi Kumar (sample)", "Officer"),
    "local-demo-admin": ("demo.admin@civictrack.local", "Demo Admin", "Admin"),
}
SUBS = {}


def provision():
    ddb = boto3.client("dynamodb")
    ddb.create_table(TableName=app.TABLE_NAME, BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "complaintId", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "complaintId", "AttributeType": "S"},
                                           {"AttributeName": "citizenSub", "AttributeType": "S"},
                                           {"AttributeName": "createdAt", "AttributeType": "S"}],
                     GlobalSecondaryIndexes=[{"IndexName": app.CITIZEN_INDEX, "Projection": {"ProjectionType": "ALL"},
                                              "KeySchema": [{"AttributeName": "citizenSub", "KeyType": "HASH"},
                                                            {"AttributeName": "createdAt", "KeyType": "RANGE"}]}])
    ddb.create_table(TableName=app.CONFIG_TABLE_NAME, BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                     AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}])
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=app.S3_BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
    s3.put_bucket_cors(Bucket=app.S3_BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": [FRONTEND_ORIGIN], "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"]}]})
    boto3.client("ses").verify_email_identity(EmailAddress=app.SES_FROM_EMAIL)

    cognito = boto3.client("cognito-idp")
    pool_id = cognito.create_user_pool(PoolName="Local-CivicTrack-Users", UsernameAttributes=["email"])["UserPool"]["Id"]
    for group in ("Citizen", "Officer", "Admin"):
        cognito.create_group(GroupName=group, UserPoolId=pool_id)
    for demo_sub, (email, name, group) in DEMO_USERS.items():
        cognito.admin_create_user(UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
                                  UserAttributes=[{"Name": "email", "Value": email}, {"Name": "name", "Value": name}])
        cognito.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)
        attrs = cognito.admin_get_user(UserPoolId=pool_id, Username=email)["UserAttributes"]
        SUBS[demo_sub] = next(a["Value"] for a in attrs if a["Name"] == "sub")
    app.USER_POOL_ID = pool_id


def claims_event(method, path, body, headers, query=None):
    lower = {k.lower(): v for k, v in headers.items()}
    event = {"httpMethod": method, "path": path, "headers": dict(headers), "body": body, "requestContext": {}, "queryStringParameters": query}
    role = lower.get("x-local-demo-role")
    if role in ("Citizen", "Officer", "Admin"):
        event["requestContext"]["authorizer"] = {"claims": {
            "sub": SUBS.get(lower.get("x-local-demo-sub") or f"local-demo-{role.lower()}", lower.get("x-local-demo-sub") or ""),
            "email": lower.get("x-local-demo-email") or f"demo.{role.lower()}@civictrack.local",
            "cognito:groups": role,
        }}
    return event


def invoke(method, path, role, sub, body=None):
    headers = {"X-Local-Demo-Role": role, "X-Local-Demo-Sub": sub, "X-Local-Demo-Email": f"{sub}@civictrack.local"}
    result = app.lambda_handler(claims_event(method, path, json.dumps(body) if body is not None else None, headers), None)
    return result["statusCode"], json.loads(result["body"])


def seed():
    """Sample records created through the real API code path. Titles are marked."""
    samples = [
        ("[Sample] Deep pothole at bus stop", "Roads", "100 Feet Rd, Indiranagar", 12.9719, 77.6412),
        ("[Sample] Streetlight out for a week", "Streetlights", "CMH Rd, Indiranagar", 12.9784, 77.6385),
        ("[Sample] Garbage not collected", "Sanitation", "Domlur Layout", 12.9610, 77.6387),
        ("[Sample] Waterlogging after rain", "Waterlogging", "Ejipura Main Rd", 12.9450, 77.6260),
        ("[Sample] No water supply since morning", "Water Supply", "HAL 2nd Stage", 12.9698, 77.6499),
        ("[Sample] Overflowing drain", "Sanitation", "Near Domlur flyover", 12.9612, 77.6390),
    ]
    ids = []
    for title, category, location, lat, lng in samples:
        _, data = invoke("POST", "/complaints", "Citizen", "local-demo-citizen", {
            "title": title, "category": category, "location": location,
            "description": "Sample record for local development. Not real data.", "latitude": lat, "longitude": lng})
        ids.append(data["complaint"]["complaintId"])
    table = boto3.resource("dynamodb").Table(app.TABLE_NAME)
    now = datetime.now(timezone.utc)
    # Backdate some samples so SLA states are visible locally.
    for cid, hours in zip(ids, (2, 45, 60, 30, 5, 1)):
        table.update_item(Key={"complaintId": cid}, UpdateExpression="SET createdAt = :c",
                          ExpressionAttributeValues={":c": (now - timedelta(hours=hours)).isoformat()})
    invoke("PATCH", f"/complaints/{ids[0]}", "Admin", "local-demo-admin", {"zone": "East", "ward": "Ward 88 (sample)", "priority": "HIGH"})
    invoke("PATCH", f"/complaints/{ids[1]}", "Admin", "local-demo-admin", {"zone": "East", "ward": "Ward 80 (sample)"})
    invoke("PATCH", f"/complaints/{ids[2]}", "Admin", "local-demo-admin", {"zone": "South", "ward": "Ward 112 (sample)"})
    for cid in ids[1:4]:
        invoke("PATCH", f"/complaints/{cid}/assign", "Admin", "local-demo-admin", {"officerSub": SUBS["local-demo-officer"]})
    invoke("PATCH", f"/complaints/{ids[2]}/start", "Officer", "local-demo-officer", {"note": "Crew dispatched"})
    invoke("PATCH", f"/complaints/{ids[3]}/start", "Officer", "local-demo-officer")
    key = f"officers/{SUBS['local-demo-officer']}/complaints/{ids[3]}/proof/sample.png"
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e2213c9a0000000049454e44ae426082")
    boto3.client("s3").put_object(Bucket=app.S3_BUCKET, Key=key, Body=png, ContentType="image/png")
    invoke("PATCH", f"/complaints/{ids[3]}/resolve", "Officer", "local-demo-officer", {"proofPhotoKey": key, "note": "Drain cleared"})
    print(f"Seeded {len(ids)} sample complaints.", flush=True)


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", FRONTEND_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Local-Demo-Role,X-Local-Demo-Sub,X-Local-Demo-Email")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()} or None
        if path == "/__dev/sla-sweep" and self.command == "POST":
            status, payload, headers = 200, json.dumps(app.process_sla_checks()), {}
        else:
            result = app.lambda_handler(claims_event(self.command, path, body, dict(self.headers.items()), query), None)
            status, payload, headers = result["statusCode"], result["body"], result.get("headers", {})
        self.send_response(status)
        for key, value in headers.items():
            if not key.lower().startswith("access-control"):
                self.send_header(key, value)
        self._cors()
        self.end_headers()
        self.wfile.write(payload.encode())

    do_GET = do_POST = do_PATCH = do_PUT = _handle

    def log_message(self, fmt, *args):
        sys.stdout.write("API %s\n" % (fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="create labelled sample complaints")
    args = parser.parse_args()
    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=MOTO_PORT, verbose=False)
    moto.start()
    provision()
    if args.seed:
        seed()
    print(f"CivicTrack local API on http://127.0.0.1:{API_PORT} (AWS mocked at {MOTO_URL})", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", API_PORT), Handler).serve_forever()
    finally:
        moto.stop()


if __name__ == "__main__":
    main()
