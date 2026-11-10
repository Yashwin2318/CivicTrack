"""End-to-end workflow with real (moto) Cognito identities.

Registration, confirmation, the post-confirmation trigger and password sign-in
all run against moto's Cognito. The ID token's own claims are then passed to
the API exactly as API Gateway's Cognito authorizer would, so the backend's
role resolution is exercised against genuine token contents. All AWS state is
in-memory; no real resource is touched."""

import base64
import importlib.util
import json
from pathlib import Path

import app
from conftest import put_image, sent_count

POST_CONFIRMATION = Path(__file__).resolve().parents[1] / "post_confirmation" / "app.py"


def decode_claims(id_token):
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def api(method, path, claims, body=None, query=None):
    event = {"httpMethod": method, "path": path, "headers": {}, "queryStringParameters": query,
             "requestContext": {"authorizer": {"claims": claims}}}
    if body is not None:
        event["body"] = json.dumps(body)
    result = app.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def sign_in(cognito, email, password):
    tokens = cognito["client"].initiate_auth(ClientId=cognito["client_id"], AuthFlow="USER_PASSWORD_AUTH",
                                             AuthParameters={"USERNAME": email, "PASSWORD": password})["AuthenticationResult"]
    return decode_claims(tokens["IdToken"])


def test_complete_workflow_with_cognito_identities(cognito, aws):
    client, pool = cognito["client"], cognito["pool_id"]

    # 1. Citizen self-registers and confirms; the post-confirmation trigger adds the Citizen group.
    client.sign_up(ClientId=cognito["client_id"], Username="asha@civictrack.test", Password="Citizen#2026",
                   UserAttributes=[{"Name": "email", "Value": "asha@civictrack.test"}, {"Name": "name", "Value": "Asha"}])
    client.admin_confirm_sign_up(UserPoolId=pool, Username="asha@civictrack.test")
    username = client.admin_get_user(UserPoolId=pool, Username="asha@civictrack.test")["Username"]
    spec = importlib.util.spec_from_file_location("post_confirmation_app", POST_CONFIRMATION)
    trigger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trigger)
    trigger.lambda_handler({"triggerSource": "PostConfirmation_ConfirmSignUp", "userPoolId": pool, "userName": username}, None)

    # 2. Staff accounts exist; the admin invites a new officer through the API.
    cognito["add_user"]("chief@civictrack.test", "Admin", "Chief Admin", "Admin#2026x")
    admin = sign_in(cognito, "chief@civictrack.test", "Admin#2026x")
    status, created = api("POST", "/admin/users", admin, {"email": "ravi@civictrack.test", "name": "Ravi Kumar", "role": "Officer"})
    assert status == 201
    client.admin_set_user_password(UserPoolId=pool, Username="ravi@civictrack.test", Password="Officer#2026", Permanent=True)

    citizen = sign_in(cognito, "asha@civictrack.test", "Citizen#2026")
    officer = sign_in(cognito, "ravi@civictrack.test", "Officer#2026")
    assert "Citizen" in str(citizen.get("cognito:groups")) and "Officer" in str(officer.get("cognito:groups"))

    # 3. Citizen submits a complaint with photo evidence and a map pin; it is persisted.
    status, upload = api("POST", "/evidence/upload-url", citizen, {"contentType": "image/jpeg", "fileSize": 2048})
    assert status == 200
    put_image(aws["s3"], upload["photoKey"], size=2048)
    status, data = api("POST", "/complaints", citizen, {"title": "Overflowing garbage bin", "category": "Sanitation",
                                                         "location": "12th Main, Indiranagar", "description": "Bin overflowing for three days.",
                                                         "latitude": 12.9719, "longitude": 77.6412, "photoKey": upload["photoKey"],
                                                         "clientRequestId": "e2e-request-0001"})
    assert status == 201
    cid = data["complaint"]["complaintId"]
    assert aws["table"].get_item(Key={"complaintId": cid})["Item"]["citizenEmail"] == "asha@civictrack.test"

    # 4-5. Admin reviews it, sees evidence, assigns the officer from the directory.
    status, detail = api("GET", f"/complaints/{cid}", admin)
    assert status == 200 and detail["complaint"]["photoUrl"]
    directory = api("GET", "/officers", admin)[1]["officers"]
    ravi = next(o for o in directory if o["email"] == "ravi@civictrack.test")
    assert api("PATCH", f"/complaints/{cid}/assign", admin, {"officerSub": ravi["sub"]})[0] == 200

    # 6. Officer receives the assignment (list + notification centre); citizen cannot act as officer.
    assert [c["complaintId"] for c in api("GET", "/complaints", officer)[1]["complaints"]] == [cid]
    assert "assigned" in [n["action"] for n in api("GET", "/notifications", officer)[1]["notifications"]]
    assert api("PATCH", f"/complaints/{cid}/start", citizen)[0] == 403

    # 7-9. Officer accepts, starts, uploads resolution evidence and resolves.
    assert api("PATCH", f"/complaints/{cid}/accept", officer)[0] == 200
    assert api("PATCH", f"/complaints/{cid}/start", officer, {"note": "Crew on site"})[0] == 200
    status, proof = api("POST", f"/complaints/{cid}/proof-upload-url", officer, {"contentType": "image/jpeg"})
    put_image(aws["s3"], proof["proofPhotoKey"])
    status, data = api("PATCH", f"/complaints/{cid}/resolve", officer, {"proofPhotoKey": proof["proofPhotoKey"], "note": "Bin emptied and area cleaned"})
    assert status == 200

    # 10-11. Citizen sees the final status and proof; notification sent and recorded.
    final = api("GET", f"/complaints/{cid}", citizen)[1]["complaint"]
    assert final["status"] == "RESOLVED" and final["proofPhotoUrl"] and final["resolutionNote"] == "Bin emptied and area cleaned"
    assert final["lastCitizenNotification"] == {**final["lastCitizenNotification"], "kind": "resolved", "sent": True}
    assert sent_count(aws["ses"]) >= 5
    assert api("PATCH", f"/complaints/{cid}/close", citizen, {"rating": 5, "feedback": "Thank you"})[0] == 200

    # 12. Admin analytics reflect the change; the audit log has the officer invitation.
    stats = api("GET", "/analytics", admin)[1]["summary"]
    assert stats["total"] == 1 and stats["resolved"] == 1 and stats["slaMet"] == 1
    assert "user_created" in [e["action"] for e in api("GET", "/admin/audit", admin)[1]["entries"]]

    actions = [h["action"] for h in aws["table"].get_item(Key={"complaintId": cid})["Item"]["history"]]
    assert actions == ["created", "assigned", "accepted", "work_started", "resolved", "closed"]
