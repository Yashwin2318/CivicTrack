"""Shared fixtures. Every AWS call is served by moto's in-memory mock:
no test can reach the real CivicTrack table, bucket, Cognito pool or SES."""

import json
import os
import sys
from pathlib import Path

import pytest

# Configure the Lambda environment BEFORE importing app (it reads env at import).
os.environ.update({
    "AWS_REGION": "ap-south-1",
    "AWS_DEFAULT_REGION": "ap-south-1",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "COMPLAINTS_TABLE": "Test-CivicTrack-Complaints",
    "CONFIG_TABLE": "Test-CivicTrack-Config",
    "S3_BUCKET": "test-civictrack-evidence",
    "USER_POOL_ID": "ap-south-1_testpool",
    "SES_FROM_EMAIL": "noreply@civictrack.test",
    "ADMIN_NOTIFICATION_EMAIL": "admin@civictrack.test",
    "ALLOWED_ORIGIN": "http://localhost:5173",
    "SLA_WARNING_HOURS": "4",
})
os.environ.pop("AWS_SAM_LOCAL", None)
os.environ.pop("AWS_PROFILE", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "complaints"))

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import app  # noqa: E402

REAL_LIST_GROUP_USERS = app.list_group_users

OFFICERS = [
    {"sub": "officer-1", "email": "officer1@civictrack.test", "name": "Officer One", "username": "o1", "enabled": True, "status": "CONFIRMED"},
    {"sub": "officer-2", "email": "officer2@civictrack.test", "name": "Officer Two", "username": "o2", "enabled": True, "status": "CONFIRMED"},
    {"sub": "officer-disabled", "email": "off3@civictrack.test", "name": "Disabled", "username": "o3", "enabled": False, "status": "CONFIRMED"},
]
ADMINS = [{"sub": "admin-1", "email": "admin@civictrack.test", "name": "Admin", "username": "a1", "enabled": True, "status": "CONFIRMED"}]


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        ddb.create_table(
            TableName=app.TABLE_NAME,
            KeySchema=[{"AttributeName": "complaintId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "complaintId", "AttributeType": "S"},
                                  {"AttributeName": "citizenSub", "AttributeType": "S"},
                                  {"AttributeName": "createdAt", "AttributeType": "S"}],
            GlobalSecondaryIndexes=[{"IndexName": app.CITIZEN_INDEX,
                                     "KeySchema": [{"AttributeName": "citizenSub", "KeyType": "HASH"}, {"AttributeName": "createdAt", "KeyType": "RANGE"}],
                                     "Projection": {"ProjectionType": "ALL"}}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=app.CONFIG_TABLE_NAME,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        import reference
        reference.invalidate()
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket=app.S3_BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        ses = boto3.client("ses", region_name="ap-south-1")
        ses.verify_email_identity(EmailAddress=app.SES_FROM_EMAIL)

        calls = {"list_group_users": 0}

        def fake_list_group_users(group):
            calls["list_group_users"] += 1
            return {"Officer": OFFICERS, "Admin": ADMINS}.get(group, [])

        monkeypatch.setattr(app, "list_group_users", fake_list_group_users)
        yield {"table": ddb.Table(app.TABLE_NAME), "config": ddb.Table(app.CONFIG_TABLE_NAME), "s3": s3, "ses": ses, "calls": calls}
        reference.invalidate()


def api_event(method, path, role=None, sub=None, body=None, email=None, groups_format="list", query=None):
    event = {"httpMethod": method, "path": path, "headers": {}, "requestContext": {}, "queryStringParameters": query}
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    if role:
        groups = [role] if groups_format == "list" else (f"[{role}]" if groups_format == "bracket" else role)
        event["requestContext"]["authorizer"] = {"claims": {
            "sub": sub or f"{role.lower()}-1",
            "email": email or f"{(sub or role.lower())}@civictrack.test",
            "cognito:groups": groups,
        }}
    return event


def call(method, path, role=None, sub=None, body=None, **kwargs):
    result = app.lambda_handler(api_event(method, path, role, sub, body, **kwargs), None)
    return result["statusCode"], json.loads(result["body"])


def put_image(s3, key, content_type="image/jpeg", size=1024):
    s3.put_object(Bucket=app.S3_BUCKET, Key=key, Body=b"x" * size, ContentType=content_type)
    return key


def sent_count(ses):
    return int(ses.get_send_quota()["SentLast24Hours"])


@pytest.fixture
def make_complaint(aws):
    """Create a complaint through the real API as citizen-1."""
    def _make(sub="citizen-1", **overrides):
        body = {"title": "Pothole on 5th Cross", "category": "Roads", "location": "5th Cross, Indiranagar",
                "description": "Large pothole causing accidents"}
        body.update(overrides)
        status, data = call("POST", "/complaints", "Citizen", sub, body)
        assert status == 201, data
        return data["complaint"]
    return _make


@pytest.fixture
def cognito(aws, monkeypatch):
    """A real moto Cognito pool with CivicTrack's groups; restores the real directory lookup."""
    client = boto3.client("cognito-idp", region_name="ap-south-1")
    pool_id = client.create_user_pool(PoolName="Test-CivicTrack-Users", UsernameAttributes=["email"],
                                      AutoVerifiedAttributes=["email"])["UserPool"]["Id"]
    for group in ("Citizen", "Officer", "Admin"):
        client.create_group(GroupName=group, UserPoolId=pool_id)
    client_id = client.create_user_pool_client(UserPoolId=pool_id, ClientName="web",
                                               ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])["UserPoolClient"]["ClientId"]
    monkeypatch.setattr(app, "USER_POOL_ID", pool_id)
    monkeypatch.setattr(app, "list_group_users", REAL_LIST_GROUP_USERS)

    def add_user(email, group, name="Test User", password="Passw0rd!x"):
        client.admin_create_user(UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
                                 UserAttributes=[{"Name": "email", "Value": email}, {"Name": "name", "Value": name}])
        client.admin_set_user_password(UserPoolId=pool_id, Username=email, Password=password, Permanent=True)
        client.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)
        attrs = client.admin_get_user(UserPoolId=pool_id, Username=email)["UserAttributes"]
        return next(a["Value"] for a in attrs if a["Name"] == "sub")

    return {"client": client, "pool_id": pool_id, "client_id": client_id, "add_user": add_user}
