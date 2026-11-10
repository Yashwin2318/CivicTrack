"""Officer accept/decline/progress, admin reject/escalate, reopen rules,
pagination, rate limiting, duplicate detection, notifications, analytics,
second-level SLA escalation and failure handling."""

from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

import app
from conftest import call, put_image
from test_sla_and_notifications import NOW, get, seed


def assign(cid, officer="officer-1"):
    return call("PATCH", f"/complaints/{cid}/assign", "Admin", "admin-1", {"officerSub": officer})


def officer(cid, action, body=None, sub="officer-1"):
    return call("PATCH", f"/complaints/{cid}/{action}", "Officer", sub, body)


def test_accept_then_start(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    status, data = officer(cid, "accept")
    assert status == 200 and data["complaint"]["acceptedAt"] and data["complaint"]["status"] == "ASSIGNED"
    assert officer(cid, "accept")[0] == 409
    assert officer(cid, "accept", sub="officer-2")[0] == 403
    assert officer(cid, "start")[0] == 200
    assert officer(cid, "accept")[0] == 409  # not allowed once in progress
    item = get(aws["table"], cid)
    assert item["lastCitizenNotification"]["kind"] == "started"


def test_decline_requires_reason_and_returns_to_pending(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    assert officer(cid, "decline", {"reason": "short"})[0] == 400
    status, data = officer(cid, "decline", {"reason": "Outside my ward, belongs to East zone team"})
    assert status == 200 and data["complaint"]["status"] == "PENDING" and data["complaint"]["assignedOfficerSub"] == ""
    assert data["complaint"]["history"][-1]["action"] == "assignment_declined"
    assert call("GET", "/complaints", "Officer", "officer-1")[1]["count"] == 0  # access removed


def test_progress_updates_with_photo(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    assert officer(cid, "progress", {"note": "early"})[0] == 409  # must be in progress
    officer(cid, "start")
    assert officer(cid, "progress", {})[0] == 400
    assert officer(cid, "progress", {"note": "Half done", "percentComplete": 150})[0] == 400
    status, data = call("POST", f"/complaints/{cid}/proof-upload-url", "Officer", "officer-1", {"contentType": "image/png", "stage": "progress"})
    assert status == 200 and "/progress/" in data["photoKey"]
    put_image(aws["s3"], data["photoKey"], "image/png")
    status, data = officer(cid, "progress", {"note": "Excavation complete", "percentComplete": 50, "photoKey": data["photoKey"]})
    c = data["complaint"]
    assert status == 200 and c["percentComplete"] == 50 and len(c["progressPhotoUrls"]) == 1
    assert c["history"][-1]["action"] == "progress_update"
    citizen_view = call("GET", f"/complaints/{cid}", "Citizen", "citizen-1")[1]["complaint"]
    assert citizen_view["percentComplete"] == 50 and citizen_view["progressPhotoUrls"]


def test_admin_reject_with_reason_and_duplicate_link(aws, make_complaint):
    original = make_complaint()["complaintId"]
    dup = make_complaint()["complaintId"]
    assert call("PATCH", f"/complaints/{dup}/reject", "Admin", body={"reason": "dup"})[0] == 400
    assert call("PATCH", f"/complaints/{dup}/reject", "Admin", body={"reason": "Duplicate of existing report", "duplicateOf": dup})[0] == 400
    status, data = call("PATCH", f"/complaints/{dup}/reject", "Admin", body={"reason": "Duplicate of existing report", "duplicateOf": original})
    assert status == 200 and data["complaint"]["status"] == "REJECTED" and data["complaint"]["duplicateOf"] == original
    assert data["complaint"]["sla"]["state"] == "not_applicable"
    assert call("PATCH", f"/complaints/{dup}/reopen", "Citizen", "citizen-1", {"reason": "please look again"})[0] == 409
    assert call("PATCH", f"/complaints/{dup}/reject", "Officer", body={"reason": "Not my job at all"})[0] == 403
    citizen = call("GET", f"/complaints/{dup}", "Citizen", "citizen-1")[1]["complaint"]
    assert citizen["rejectionReason"] == "Duplicate of existing report"
    assert get(aws["table"], dup)["lastCitizenNotification"]["kind"] == "rejected"


def test_manual_escalation_raises_priority(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assert call("PATCH", f"/complaints/{cid}/escalate", "Admin", body={})[0] == 400
    status, data = call("PATCH", f"/complaints/{cid}/escalate", "Admin", body={"reason": "Ward councillor complaint"})
    first_priority = data["complaint"]["priority"]
    status, data = call("PATCH", f"/complaints/{cid}/escalate", "Admin", body={"reason": "Still pending after site visit"})
    c = data["complaint"]
    assert status == 200 and c["escalated"] is True and c["escalationLevel"] == 2
    assert app.lc.PRIORITIES.index(c["priority"]) >= app.lc.PRIORITIES.index(first_priority)
    assert call("PATCH", f"/complaints/{cid}/escalate", "Citizen", "citizen-1", {"reason": "please hurry"})[0] == 403


def _resolve(aws, cid):
    assign(cid)
    officer(cid, "start")
    data = call("POST", f"/complaints/{cid}/proof-upload-url", "Officer", "officer-1", {"contentType": "image/jpeg"})[1]
    put_image(aws["s3"], data["proofPhotoKey"])
    assert officer(cid, "resolve", {"proofPhotoKey": data["proofPhotoKey"]})[0] == 200


def test_reopen_window_and_limit(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    _resolve(aws, cid)
    aws["table"].update_item(Key={"complaintId": cid}, UpdateExpression="SET resolvedAt = :r",
                             ExpressionAttributeValues={":r": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()})
    status, data = call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-1", {"reason": "still broken"})
    assert status == 409 and "14 days" in data["error"]
    cid2 = make_complaint()["complaintId"]
    _resolve(aws, cid2)
    aws["table"].update_item(Key={"complaintId": cid2}, UpdateExpression="SET reopenCount = :n", ExpressionAttributeValues={":n": 3})
    assert call("PATCH", f"/complaints/{cid2}/reopen", "Citizen", "citizen-1", {"reason": "still broken"})[0] == 409


def test_close_with_feedback(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    _resolve(aws, cid)
    status, data = call("PATCH", f"/complaints/{cid}/close", "Citizen", "citizen-1", {"rating": 5, "feedback": "Quick and clean work"})
    assert status == 200 and data["complaint"]["citizenRating"] == 5 and data["complaint"]["citizenFeedback"] == "Quick and clean work"


def test_priority_scoring_and_summary_on_create(aws, make_complaint):
    c = make_complaint(title="Live wire sparking near school", category="Streetlights",
                       description="Exposed wire hanging. Children walk here every day.", citizenUrgency="HIGH")
    assert c["priority"] in ("HIGH", "CRITICAL") and c["summary"].startswith("Live wire sparking near school:")
    assert "priorityReasons" not in c  # internal triage detail hidden from citizens
    admin_view = call("GET", f"/complaints/{c['complaintId']}", "Admin")[1]["complaint"]
    assert admin_view["priorityScore"] >= 50 and admin_view["priorityReasons"]
    assert call("POST", "/complaints", "Citizen", body={"title": "t", "category": "Roads", "location": "l", "description": "d", "citizenUrgency": "URGENT"})[0] == 400


def test_duplicate_detection_hides_other_citizens_content(aws, make_complaint):
    mine = make_complaint("citizen-1", latitude=12.9716, longitude=77.5946)["complaintId"]
    make_complaint("citizen-2", latitude=12.9717, longitude=77.5947, title="Private details of someone else")
    status, data = call("POST", "/complaints/similar", "Citizen", "citizen-1",
                        {"title": "Big pothole", "category": "Roads", "latitude": 12.97165, "longitude": 77.59465})
    assert status == 200 and len(data["duplicates"]) == 2
    others = [d for d in data["duplicates"] if not d["yours"]]
    assert others and "title" not in others[0] and "complaintId" not in others[0]
    assert any(d.get("complaintId") == mine for d in data["duplicates"])
    assert data["suggestedCategory"]["category"] == "Roads" and "reasons" not in data["priority"]
    admin = call("POST", "/complaints/similar", "Admin", body={"title": "pothole", "latitude": 12.97165, "longitude": 77.59465})[1]
    assert all("complaintId" in d for d in admin["duplicates"]) and admin["priority"]["reasons"]
    far = call("POST", "/complaints/similar", "Citizen", body={"title": "x", "category": "Roads", "latitude": 13.5, "longitude": 77.9})[1]
    assert far["duplicates"] == []


def test_citizen_rate_limit(aws, make_complaint):
    call("PUT", "/admin/settings", "Admin", body={"maxComplaintsPerHour": 2})
    make_complaint(); make_complaint()
    status, data = call("POST", "/complaints", "Citizen", "citizen-1", {"title": "t", "category": "Roads", "location": "l", "description": "d"})
    assert status == 429 and data["code"] == "RATE_LIMITED"
    assert call("POST", "/complaints", "Citizen", "citizen-2", {"title": "t", "category": "Roads", "location": "l", "description": "d"})[0] == 201


@pytest.mark.parametrize("role,sub", [("Admin", "admin-1"), ("Citizen", "citizen-1")])
def test_pagination(aws, make_complaint, role, sub):
    for _ in range(5):
        make_complaint()
    seen, token = [], None
    while True:
        query = {"limit": "2", **({"nextToken": token} if token else {})}
        status, data = call("GET", "/complaints", role, sub, query=query)
        assert status == 200
        seen += [c["complaintId"] for c in data["complaints"]]
        token = data["nextToken"]
        if not token:
            break
    assert len(seen) == 5 and len(set(seen)) == 5
    assert call("GET", "/complaints", role, sub, query={"limit": "0"})[0] == 400
    assert call("GET", "/complaints", role, sub, query={"limit": "2", "nextToken": "!!notbase64"})[0] == 400


def test_citizen_listing_falls_back_without_index(aws, make_complaint, monkeypatch):
    make_complaint()
    monkeypatch.setattr(app, "CITIZEN_INDEX", "missing-index")
    assert call("GET", "/complaints", "Citizen", "citizen-1")[1]["count"] == 1


def test_notification_centre_per_role(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    officer(cid, "accept")
    officer(cid, "start")
    citizen = [n["action"] for n in call("GET", "/notifications", "Citizen", "citizen-1")[1]["notifications"]]
    assert set(citizen) == {"assigned", "accepted", "work_started"}
    officer_feed = [n["action"] for n in call("GET", "/notifications", "Officer", "officer-1")[1]["notifications"]]
    assert officer_feed == ["assigned"]  # own actions are not echoed back
    admin_feed = [n["action"] for n in call("GET", "/notifications", "Admin", "admin-2")[1]["notifications"]]
    assert "created" in admin_feed
    assert call("GET", "/notifications", "Citizen", "citizen-2")[1]["notifications"] == []


def test_analytics_endpoint_reflects_lifecycle(aws, make_complaint):
    cid = make_complaint(latitude=12.97, longitude=77.59)["complaintId"]
    make_complaint()
    _resolve(aws, cid)
    status, data = call("GET", "/analytics", "Admin", query={"granularity": "week"})
    s = data["summary"]
    assert status == 200 and s["total"] == 2 and s["resolved"] == 1 and s["open"] == 1 and data["mapped"] == 1
    assert data["byOfficer"][0]["key"] == "Officer One"
    assert call("GET", "/analytics", "Admin", query={"from": "09-11-2026"})[0] == 400
    assert call("GET", "/analytics", "Admin", query={"granularity": "hour"})[0] == 400


def test_second_level_escalation_once(aws):
    seed(aws["table"], "CT-LEVEL20001", hours_ago=80)
    first = app.process_sla_checks(NOW)                      # breach notice
    second = app.process_sla_checks(NOW + timedelta(minutes=15))  # level 2 (>24 h past deadline)
    third = app.process_sla_checks(NOW + timedelta(minutes=30))
    assert first["newEscalations"] == 1 and second["secondLevelEscalations"] == 1 and third["notificationEmailsSent"] == 0
    item = get(aws["table"], "CT-LEVEL20001")
    assert item["escalationLevel"] == 2 and [h["action"] for h in item["history"]] == ["sla_escalated", "sla_escalated_level2"]


def test_sla_uses_configured_warning_hours(aws):
    call("PUT", "/admin/settings", "Admin", body={"warningHours": 10})
    seed(aws["table"], "CT-WARNCONF01", hours_ago=39)  # 9 h left: inside a 10 h window, outside the default 4 h
    assert app.process_sla_checks(NOW)["warningsSent"] == 1


# ---------------- failure handling ----------------

def test_database_failure_returns_generic_500(aws, monkeypatch):
    class Broken:
        def scan(self, **_):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "internal detail"}}, "Scan")
    monkeypatch.setattr(app, "get_table", lambda: Broken())
    status, data = call("GET", "/complaints", "Admin")
    assert status == 500 and data == {"error": "Internal server error", "code": "INTERNAL_ERROR"}


def test_config_table_outage_falls_back_to_defaults(aws, monkeypatch, make_complaint):
    class BrokenConfig:
        def query(self, **_):
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no table"}}, "Query")
    app.reference.invalidate()
    monkeypatch.setattr(app, "get_config_table", lambda: BrokenConfig())
    c = make_complaint()
    assert c["department"] == "Public Works" and c["slaHours"] == 48


def test_s3_outage_on_upload_validation_is_500_not_leak(aws, monkeypatch):
    class BrokenS3:
        def head_object(self, **_):
            raise ClientError({"Error": {"Code": "SlowDown", "Message": "detail"}}, "HeadObject")
    monkeypatch.setattr(app, "get_s3_client", lambda for_presign=False: BrokenS3())
    status, data = call("POST", "/complaints", "Citizen", body={"title": "t", "category": "Roads", "location": "l", "description": "d",
                                                                  "photoKey": "citizens/citizen-1/complaint-evidence/x.jpg"})
    assert status == 500 and "detail" not in str(data)


def test_oversized_body_rejected(aws):
    status, data = call("POST", "/complaints", "Citizen", body="x" * (1024 * 1024 + 1))
    assert status == 413 and data["code"] == "PAYLOAD_TOO_LARGE"
