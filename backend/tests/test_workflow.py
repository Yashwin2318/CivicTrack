"""Integration tests for the full complaint lifecycle, invalid transitions,
assignment isolation and concurrent-update protection."""

import pytest

import app
from conftest import call, put_image, sent_count


def assign(cid, officer="officer-1", **extra):
    return call("PATCH", f"/complaints/{cid}/assign", "Admin", "admin-1", {"officerSub": officer, **extra})


def start(cid, officer="officer-1", body=None):
    return call("PATCH", f"/complaints/{cid}/start", "Officer", officer, body)


def upload_proof(aws, cid, officer="officer-1", stage="after"):
    status, data = call("POST", f"/complaints/{cid}/proof-upload-url", "Officer", officer, {"contentType": "image/jpeg", "stage": stage})
    assert status == 200, data
    put_image(aws["s3"], data["proofPhotoKey"])
    return data["proofPhotoKey"]


def resolve(cid, key, officer="officer-1", note=None):
    return call("PATCH", f"/complaints/{cid}/resolve", "Officer", officer, {"proofPhotoKey": key, **({"note": note} if note else {})})


def test_full_lifecycle_end_to_end(aws, make_complaint):
    """Citizen submits -> Admin assigns -> Officer starts with before-photo ->
    uploads proof -> resolves -> citizen notified -> citizen reopens ->
    officer reworks -> resolves -> citizen closes with rating."""
    key = put_image(aws["s3"], "citizens/citizen-1/complaint-evidence/p.jpg")
    c = make_complaint(photoKey=key)
    cid = c["complaintId"]

    status, data = assign(cid)
    assert status == 200 and data["complaint"]["status"] == "ASSIGNED"
    assert data["complaint"]["assignedOfficerName"] == "Officer One"  # name from Cognito, not request

    before = upload_proof(aws, cid, stage="before")
    status, data = start(cid, body={"beforePhotoKey": before, "note": "On site"})
    assert status == 200 and data["complaint"]["status"] == "IN_PROGRESS" and data["complaint"]["beforePhotoKey"] == before

    proof = upload_proof(aws, cid)
    status, data = resolve(cid, proof, note="Pothole filled")
    assert status == 200
    resolved = data["complaint"]
    assert resolved["status"] == "RESOLVED" and resolved["resolutionNote"] == "Pothole filled"
    assert resolved["proofPhotoUrl"].startswith("https://")
    assert resolved["lastCitizenNotification"]["kind"] == "resolved"
    assert resolved["lastCitizenNotification"]["sent"] is True

    status, data = call("GET", f"/complaints/{cid}", "Citizen", "citizen-1")
    assert data["complaint"]["proofPhotoUrl"] and data["complaint"]["photoUrl"]

    status, data = call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-1", {"reason": "Pothole reappeared after rain"})
    assert status == 200 and data["complaint"]["status"] == "REOPENED" and data["complaint"]["reopenCount"] == 1
    assert data["complaint"]["slaStartedAt"]

    assert start(cid)[0] == 200
    assert resolve(cid, upload_proof(aws, cid))[0] == 200

    status, data = call("PATCH", f"/complaints/{cid}/close", "Citizen", "citizen-1", {"rating": 4})
    assert status == 200 and data["complaint"]["status"] == "CLOSED" and data["complaint"]["citizenRating"] == 4

    actions = [h["action"] for h in aws["table"].get_item(Key={"complaintId": cid})["Item"]["history"]]
    assert actions == ["created", "assigned", "work_started", "resolved", "reopened", "work_started", "resolved", "closed"]
    assert sent_count(aws["ses"]) > 0


def test_legacy_pending_with_officer_can_start(aws, make_complaint):
    """Rows written before the lifecycle extension: PENDING + officer = ASSIGNED."""
    c = make_complaint()
    aws["table"].update_item(Key={"complaintId": c["complaintId"]},
                             UpdateExpression="SET assignedOfficerSub = :o, assignedOfficerName = :n REMOVE history",
                             ExpressionAttributeValues={":o": "officer-1", ":n": "Officer One"})
    status, data = start(c["complaintId"])
    assert status == 200 and data["complaint"]["status"] == "IN_PROGRESS"
    assert len(data["complaint"]["history"]) == 1  # history created on first action


@pytest.mark.parametrize("setup,action,expected", [
    ("pending", "start", 403),        # not assigned to this officer
    ("pending", "resolve", 403),
    ("assigned", "resolve", 409),     # must start first
    ("assigned", "reopen", 409),
    ("assigned", "close", 409),
    ("in_progress", "start", 409),
    ("in_progress", "unassign", 409),
    ("in_progress", "assign", 409),
    ("pending", "unassign", 409),
])
def test_invalid_transitions(aws, make_complaint, setup, action, expected):
    cid = make_complaint()["complaintId"]
    if setup in ("assigned", "in_progress"):
        assign(cid)
    if setup == "in_progress":
        start(cid)
    if action == "start":
        status = start(cid)[0]
    elif action == "resolve":
        status = resolve(cid, "officers/officer-1/complaints/x/proof/a.jpg")[0]
    elif action == "reopen":
        status = call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-1", {"reason": "still broken"})[0]
    elif action == "close":
        status = call("PATCH", f"/complaints/{cid}/close", "Citizen", "citizen-1", {})[0]
    elif action == "unassign":
        status = call("PATCH", f"/complaints/{cid}/unassign", "Admin", body={})[0]
    else:
        status = assign(cid, "officer-2")[0]
    assert status == expected


def test_other_officer_cannot_act(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid, "officer-1")
    assert start(cid, "officer-2")[0] == 403
    assert call("POST", f"/complaints/{cid}/proof-upload-url", "Officer", "officer-2", {"contentType": "image/png"})[0] == 403


def test_proof_must_be_in_officer_prefix(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid); start(cid)
    foreign = put_image(aws["s3"], f"officers/officer-2/complaints/{cid}/proof/x.jpg")
    citizen = put_image(aws["s3"], "citizens/citizen-1/complaint-evidence/x.jpg")
    assert resolve(cid, foreign)[0] == 400
    assert resolve(cid, citizen)[0] == 400
    assert resolve(cid, "")[0] == 400


def test_proof_upload_requires_in_progress(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    assert call("POST", f"/complaints/{cid}/proof-upload-url", "Officer", "officer-1", {"contentType": "image/png"})[0] == 409


def test_assignment_rules(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assert assign(cid, "not-an-officer")[0] == 400
    assert assign(cid, "officer-disabled")[0] == 400
    assert assign(cid, "")[0] == 400
    assert call("PATCH", f"/complaints/{cid}/assign", "Officer", "officer-1", {"officerSub": "officer-1"})[0] == 403
    assert assign(cid, "officer-1")[0] == 200
    assert assign(cid, "officer-1")[0] == 409  # same officer
    status, data = assign(cid, "officer-2", reason="Workload balancing")
    assert status == 200 and data["complaint"]["assignedOfficerSub"] == "officer-2"
    assert data["complaint"]["history"][-1]["action"] == "reassigned"
    assert data["complaint"]["history"][-1]["previousOfficerName"] == "Officer One"


def test_unassign_returns_to_pending(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    status, data = call("PATCH", f"/complaints/{cid}/unassign", "Admin", body={"reason": "Wrong ward"})
    assert status == 200
    assert data["complaint"]["status"] == "PENDING" and data["complaint"]["assignedOfficerSub"] == ""
    assert call("GET", "/complaints", "Officer", "officer-1")[1]["count"] == 0
    assert call("PATCH", f"/complaints/{cid}/unassign", "Citizen", "citizen-1", {})[0] == 403


def test_reopen_rules(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid); start(cid); resolve(cid, upload_proof(aws, cid))
    assert call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-1", {"reason": "no"})[0] == 400
    assert call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-2", {"reason": "not mine"})[0] == 404
    assert call("PATCH", f"/complaints/{cid}/reopen", "Admin", body={"reason": "admin cannot"})[0] == 403
    assert call("PATCH", f"/complaints/{cid}/close", "Officer", "officer-1", {})[0] == 403
    assert call("PATCH", f"/complaints/{cid}/close", "Citizen", "citizen-1", {"rating": 9})[0] == 400
    assert call("PATCH", f"/complaints/{cid}/close", "Admin", body={})[0] == 200


def test_reassign_after_reopen(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid); start(cid); resolve(cid, upload_proof(aws, cid))
    call("PATCH", f"/complaints/{cid}/reopen", "Citizen", "citizen-1", {"reason": "issue persists"})
    status, data = assign(cid, "officer-2")
    assert status == 200 and data["complaint"]["status"] == "ASSIGNED"
    assert start(cid, "officer-1")[0] == 403
    assert start(cid, "officer-2")[0] == 200


def test_concurrent_update_is_rejected(aws, make_complaint):
    """Two admins read the same PENDING complaint; the second write must fail."""
    cid = make_complaint()["complaintId"]
    stale = aws["table"].get_item(Key={"complaintId": cid})["Item"]
    assert assign(cid, "officer-1")[0] == 200
    user = {"role": "Admin", "sub": "admin-2"}
    with pytest.raises(app.RequestError) as excinfo:
        app.apply_transition(aws["table"], stale, {"status": "ASSIGNED", "assignedOfficerSub": "officer-2"},
                             app.history_entry("assigned", user, "PENDING", "ASSIGNED"))
    assert excinfo.value.status_code == 409
    assert aws["table"].get_item(Key={"complaintId": cid})["Item"]["assignedOfficerSub"] == "officer-1"


def test_double_start_request_only_applies_once(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    assign(cid)
    assert start(cid)[0] == 200
    assert start(cid)[0] == 409
    history = aws["table"].get_item(Key={"complaintId": cid})["Item"]["history"]
    assert [h["action"] for h in history].count("work_started") == 1
