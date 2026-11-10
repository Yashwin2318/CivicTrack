"""API, authentication, authorization, validation and upload tests."""

import json

import pytest

import app
from conftest import api_event, call, put_image


# ---------------- authentication & routing ----------------

def test_health_is_public(aws):
    assert call("GET", "/health") == (200, {"status": "healthy", "application": "CivicTrack API"})


def test_options_preflight(aws):
    assert call("OPTIONS", "/complaints")[0] == 200


@pytest.mark.parametrize("path", ["/complaints", "/officers", "/complaints/CT-ABCDEF1234", "/evidence/upload-url"])
def test_missing_claims_is_401(aws, path):
    assert call("GET", path)[0] == 401


def test_user_without_civictrack_group_is_403(aws):
    event = api_event("GET", "/complaints", "Citizen")
    event["requestContext"]["authorizer"]["claims"]["cognito:groups"] = ["SomethingElse"]
    assert app.lambda_handler(event, None)["statusCode"] == 403


@pytest.mark.parametrize("fmt", ["list", "bracket", "plain"])
def test_group_claim_formats(aws, fmt):
    assert call("GET", "/complaints", "Admin", groups_format=fmt)[0] == 200


def test_admin_precedence_over_other_groups(aws):
    event = api_event("GET", "/officers", "Citizen")
    event["requestContext"]["authorizer"]["claims"]["cognito:groups"] = ["Citizen", "Admin"]
    assert app.lambda_handler(event, None)["statusCode"] == 200


def test_unknown_route_and_method(aws):
    assert call("GET", "/nope", "Admin")[0] == 404
    assert call("DELETE", "/complaints", "Admin")[0] == 405


def test_cors_headers_present(aws):
    result = app.lambda_handler(api_event("GET", "/health"), None)
    assert result["headers"]["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "X-Local-Demo-Role" not in result["headers"]["Access-Control-Allow-Headers"]


def test_local_demo_headers_rejected_when_deployed(aws):
    event = api_event("GET", "/complaints")
    event["headers"] = {"X-Local-Demo-Role": "Admin", "X-Local-Demo-Sub": "x"}
    assert app.lambda_handler(event, None)["statusCode"] == 401


def test_invalid_complaint_id_rejected(aws):
    assert call("GET", "/complaints/..%2F..%2Fetc", "Admin")[0] in (400, 404)
    assert call("GET", "/complaints/not-an-id", "Admin")[0] == 400


# ---------------- create complaint ----------------

def test_create_complaint_success(aws, make_complaint):
    complaint = make_complaint(latitude=12.9716, longitude=77.5946, landmark="Near metro")
    assert complaint["complaintId"].startswith("CT-")
    assert complaint["status"] == "PENDING"
    assert complaint["department"] == "Public Works"
    assert complaint["latitude"] == pytest.approx(12.9716)
    assert complaint["history"][0]["action"] == "created"
    assert "actorSub" not in complaint["history"][0]  # stripped for citizens
    stored = aws["table"].get_item(Key={"complaintId": complaint["complaintId"]})["Item"]
    assert stored["citizenSub"] == "citizen-1"
    assert stored["citizenEmail"] == "citizen-1@civictrack.test"  # from verified claims, not body


@pytest.mark.parametrize("override,message", [
    ({"title": ""}, "title is required"),
    ({"title": "x" * 151}, "title must be 150"),
    ({"description": None}, "description is required"),
    ({"category": 5}, "category is required"),
    ({"latitude": 12.9}, "together"),
    ({"latitude": 95, "longitude": 77}, "latitude must be between"),
    ({"latitude": "abc", "longitude": 77}, "latitude must be a number"),
    ({"latitude": True, "longitude": 77}, "latitude must be a number"),
    ({"clientRequestId": "bad id!"}, "Invalid clientRequestId"),
])
def test_create_validation(aws, override, message):
    body = {"title": "t", "category": "Roads", "location": "l", "description": "d", **override}
    status, data = call("POST", "/complaints", "Citizen", "citizen-1", body)
    assert status == 400 and message in data["error"]


def test_create_rejects_bad_json_and_non_object(aws):
    assert call("POST", "/complaints", "Citizen", body="{not json")[1]["error"] == "Invalid JSON"
    assert call("POST", "/complaints", "Citizen", body="[1,2]")[1]["error"] == "Invalid request body"


@pytest.mark.parametrize("role", ["Officer", "Admin"])
def test_only_citizens_create(aws, role):
    assert call("POST", "/complaints", role, body={"title": "t", "category": "Roads", "location": "l", "description": "d"})[0] == 403


def test_create_is_idempotent_with_client_request_id(aws):
    body = {"title": "t", "category": "Roads", "location": "l", "description": "d", "clientRequestId": "req-12345678"}
    first = call("POST", "/complaints", "Citizen", "citizen-1", body)
    second = call("POST", "/complaints", "Citizen", "citizen-1", body)
    assert first[0] == 201 and second[0] == 200 and second[1]["duplicate"] is True
    assert first[1]["complaint"]["complaintId"] == second[1]["complaint"]["complaintId"]
    assert aws["table"].scan()["Count"] == 1


# ---------------- evidence upload ----------------

def test_citizen_upload_url_scoped_to_user(aws):
    status, data = call("POST", "/evidence/upload-url", "Citizen", "citizen-1", {"contentType": "image/png", "fileSize": 2048})
    assert status == 200
    assert data["photoKey"].startswith("citizens/citizen-1/complaint-evidence/") and data["photoKey"].endswith(".png")
    assert "X-Amz-Expires=300" in data["uploadUrl"]


@pytest.mark.parametrize("body,status", [
    ({"contentType": "image/gif"}, 400),
    ({"contentType": "application/pdf"}, 400),
    ({"contentType": "image/jpeg", "fileSize": 11 * 1024 * 1024}, 400),
    ({"contentType": "image/jpeg", "fileSize": -1}, 400),
])
def test_upload_url_validation(aws, body, status):
    assert call("POST", "/evidence/upload-url", "Citizen", body=body)[0] == status


def test_only_citizens_get_evidence_upload_url(aws):
    assert call("POST", "/evidence/upload-url", "Officer", body={"contentType": "image/png"})[0] == 403


def test_create_with_uploaded_photo(aws, make_complaint):
    key = put_image(aws["s3"], "citizens/citizen-1/complaint-evidence/a.jpg")
    complaint = make_complaint(photoKey=key)
    assert complaint["photoKey"] == key and complaint["photoUrl"].startswith("https://")


@pytest.mark.parametrize("key,content_type,size,message", [
    ("citizens/citizen-1/complaint-evidence/missing.jpg", None, 0, "has not been uploaded"),
    ("citizens/citizen-2/complaint-evidence/other.jpg", "image/jpeg", 10, "Invalid evidence object key"),
    ("citizens/citizen-1/complaint-evidence/doc.jpg", "text/html", 10, "JPEG, PNG or WebP"),
    ("citizens/citizen-1/complaint-evidence/big.jpg", "image/jpeg", 10 * 1024 * 1024 + 1, "10 MB"),
    ("https://evil.example/x.jpg", None, 0, "Invalid evidence object key"),
    ("citizens/citizen-1/complaint-evidence/../../x.jpg", None, 0, "Invalid evidence object key"),
])
def test_photo_validation(aws, key, content_type, size, message):
    if content_type:
        put_image(aws["s3"], key, content_type, size)
    body = {"title": "t", "category": "Roads", "location": "l", "description": "d", "photoKey": key}
    status, data = call("POST", "/complaints", "Citizen", "citizen-1", body)
    assert status == 400 and message in data["error"]


# ---------------- data isolation ----------------

def test_citizen_sees_only_own_complaints(aws, make_complaint):
    mine = make_complaint("citizen-1")
    other = make_complaint("citizen-2")
    status, data = call("GET", "/complaints", "Citizen", "citizen-1")
    assert status == 200 and [c["complaintId"] for c in data["complaints"]] == [mine["complaintId"]]
    # other citizen's complaint looks like it does not exist
    assert call("GET", f"/complaints/{other['complaintId']}", "Citizen", "citizen-1")[0] == 404
    assert call("GET", f"/complaints/{mine['complaintId']}", "Citizen", "citizen-1")[0] == 200


def test_admin_sees_all(aws, make_complaint):
    make_complaint("citizen-1"); make_complaint("citizen-2")
    assert call("GET", "/complaints", "Admin")[1]["count"] == 2


def test_officer_sees_only_assigned(aws, make_complaint):
    a = make_complaint(); b = make_complaint()
    call("PATCH", f"/complaints/{a['complaintId']}/assign", "Admin", body={"officerSub": "officer-1"})
    call("PATCH", f"/complaints/{b['complaintId']}/assign", "Admin", body={"officerSub": "officer-2"})
    data = call("GET", "/complaints", "Officer", "officer-1")[1]
    assert [c["complaintId"] for c in data["complaints"]] == [a["complaintId"]]
    assert call("GET", f"/complaints/{b['complaintId']}", "Officer", "officer-1")[0] == 403


def test_citizen_response_hides_staff_ids(aws, make_complaint):
    c = make_complaint()
    call("PATCH", f"/complaints/{c['complaintId']}/assign", "Admin", body={"officerSub": "officer-1"})
    data = call("GET", f"/complaints/{c['complaintId']}", "Citizen", "citizen-1")[1]["complaint"]
    assert "assignedOfficerSub" not in data and "assignedBySub" not in data
    assert data["assignedOfficerName"] == "Officer One"
    assert all("actorSub" not in h for h in data["history"])


# ---------------- officer directory ----------------

def test_officer_directory_admin_only(aws):
    status, data = call("GET", "/officers", "Admin")
    assert status == 200 and {o["sub"] for o in data["officers"]} == {"officer-1", "officer-2", "officer-disabled"}
    assert "username" not in data["officers"][0]
    assert call("GET", "/officers", "Officer")[0] == 403
    assert call("GET", "/officers", "Citizen")[0] == 403


# ---------------- admin metadata ----------------

def test_admin_updates_priority_zone_ward(aws, make_complaint):
    c = make_complaint()
    status, data = call("PATCH", f"/complaints/{c['complaintId']}", "Admin",
                        body={"priority": "high", "zone": "East", "ward": "Ward 80", "department": "Electrical"})
    assert status == 200
    d = data["complaint"]
    assert (d["priority"], d["zone"], d["ward"], d["department"]) == ("HIGH", "East", "Ward 80", "Electrical")
    assert d["history"][-1]["action"] == "details_updated"


@pytest.mark.parametrize("body", [{"priority": "urgent"}, {"department": "Space Agency"}, {"ward": "x" * 81}, {}, {"unknown": 1}])
def test_admin_update_validation(aws, make_complaint, body):
    c = make_complaint()
    assert call("PATCH", f"/complaints/{c['complaintId']}", "Admin", body=body)[0] == 400


@pytest.mark.parametrize("role", ["Citizen", "Officer"])
def test_metadata_update_admin_only(aws, make_complaint, role):
    c = make_complaint()
    assert call("PATCH", f"/complaints/{c['complaintId']}", role, "citizen-1", body={"priority": "HIGH"})[0] == 403


def test_internal_errors_are_generic(aws, monkeypatch):
    monkeypatch.setattr(app, "get_complaints", lambda *a: (_ for _ in ()).throw(RuntimeError("secret detail")))
    status, data = call("GET", "/complaints", "Admin")
    assert status == 500 and data == {"error": "Internal server error", "code": "INTERNAL_ERROR"}
    assert "secret" not in json.dumps(data)
