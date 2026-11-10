"""SLA automation and notification tests.

Uses only controlled synthetic records written into moto's in-memory table.
Real citizen complaints are never read or modified."""

from datetime import datetime, timedelta, timezone

import pytest

import app
from conftest import sent_count

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def seed(table, cid, hours_ago, status="ASSIGNED", **extra):
    item = {"complaintId": cid, "title": "SLA test", "category": "Roads", "location": "Test",
            "status": status, "createdAt": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "slaHours": 48, "assignedOfficerSub": "officer-1", "citizenEmail": "c@civictrack.test", **extra}
    table.put_item(Item=item)
    return item


def get(table, cid):
    return table.get_item(Key={"complaintId": cid})["Item"]


def test_scheduled_event_routes_to_sweep(aws):
    result = app.lambda_handler({"source": "aws.events", "detail-type": "Scheduled Event"}, None)
    assert result["status"] == "completed"


def test_on_track_complaint_sends_nothing_and_skips_cognito(aws):
    seed(aws["table"], "CT-ONTRACK001", hours_ago=10)
    summary = app.process_sla_checks(NOW)
    assert summary["notificationEmailsSent"] == 0
    assert aws["calls"]["list_group_users"] == 0  # fix for per-complaint Cognito lookups
    assert "slaWarningSentAt" not in get(aws["table"], "CT-ONTRACK001")


def test_warning_sent_exactly_once(aws):
    seed(aws["table"], "CT-WARN000001", hours_ago=45)
    first = app.process_sla_checks(NOW)
    second = app.process_sla_checks(NOW + timedelta(minutes=15))
    assert first["warningsSent"] == 1 and second["warningsSent"] == 0
    assert get(aws["table"], "CT-WARN000001")["slaWarningSentAt"] == NOW.isoformat()


def test_breach_escalates_once_with_history(aws):
    seed(aws["table"], "CT-BREACH0001", hours_ago=50)
    first = app.process_sla_checks(NOW)
    second = app.process_sla_checks(NOW + timedelta(minutes=15))
    item = get(aws["table"], "CT-BREACH0001")
    assert first["newEscalations"] == 1 and second["newEscalations"] == 0
    assert first["notificationEmailsSent"] == 1 and second["notificationEmailsSent"] == 0
    assert item["escalated"] is True and item["escalationNotifiedAt"]
    assert [h["action"] for h in item["history"]] == ["sla_escalated"]


def test_duplicate_eventbridge_delivery_does_not_double_send(aws):
    """Simulate two overlapping invocations that both read 'not yet sent'."""
    seed(aws["table"], "CT-DUPLICATE1", hours_ago=45)
    stamp_a, stamp_b = NOW.isoformat(), (NOW + timedelta(seconds=1)).isoformat()
    assert app.claim_flag(aws["table"], "CT-DUPLICATE1", "slaWarningSentAt", stamp_a) is True
    assert app.claim_flag(aws["table"], "CT-DUPLICATE1", "slaWarningSentAt", stamp_b) is False


def test_send_failure_releases_claim_for_retry(aws, monkeypatch):
    seed(aws["table"], "CT-RETRY00001", hours_ago=45)
    monkeypatch.setattr(app, "send_notification", lambda *a, **k: False)
    summary = app.process_sla_checks(NOW)
    assert summary["warningsSent"] == 0
    assert "slaWarningSentAt" not in get(aws["table"], "CT-RETRY00001")
    monkeypatch.undo()
    # re-apply the Cognito stub that undo() removed
    monkeypatch.setattr(app, "list_group_users", lambda g: [])
    assert app.process_sla_checks(NOW + timedelta(minutes=15))["warningsSent"] == 1


@pytest.mark.parametrize("status", ["RESOLVED", "CLOSED"])
def test_final_statuses_ignored(aws, status):
    seed(aws["table"], "CT-FINAL00001", hours_ago=100, status=status)
    summary = app.process_sla_checks(NOW)
    assert summary["scanned"] == 0 and "escalated" not in get(aws["table"], "CT-FINAL00001")


def test_malformed_and_missing_timestamps_are_counted_not_fatal(aws):
    seed(aws["table"], "CT-BADTIME001", hours_ago=1, createdAt="yesterday-ish")
    aws["table"].put_item(Item={"complaintId": "CT-NOTIME0001", "status": "PENDING"})
    seed(aws["table"], "CT-BREACH0002", hours_ago=60)
    summary = app.process_sla_checks(NOW)
    assert summary["malformedTimestamps"] == 2
    assert summary["newEscalations"] == 1  # the valid record is still processed


def test_naive_timestamp_treated_as_utc(aws):
    naive = (NOW - timedelta(hours=49)).replace(tzinfo=None).isoformat()
    seed(aws["table"], "CT-NAIVE00001", hours_ago=0, createdAt=naive)
    assert app.process_sla_checks(NOW)["newEscalations"] == 1


def test_one_failing_record_does_not_abort_sweep(aws, monkeypatch):
    seed(aws["table"], "CT-AAAAAAAA01", hours_ago=60)
    seed(aws["table"], "CT-BBBBBBBB02", hours_ago=60)
    original = app.mark_escalated
    def flaky(table, cid, stamp):
        if cid == "CT-AAAAAAAA01":
            raise RuntimeError("boom")
        return original(table, cid, stamp)
    monkeypatch.setattr(app, "mark_escalated", flaky)
    summary = app.process_sla_checks(NOW)
    assert summary["errors"] == 1 and summary["newEscalations"] == 1


def test_reopened_complaint_uses_fresh_sla(aws):
    seed(aws["table"], "CT-REOPEN0001", hours_ago=100, status="REOPENED", slaStartedAt=(NOW - timedelta(hours=2)).isoformat())
    assert app.process_sla_checks(NOW)["newEscalations"] == 0


def test_legacy_escalated_without_notification_gets_notified_once(aws):
    seed(aws["table"], "CT-LEGACY0001", hours_ago=60, status="PENDING", escalated=True)
    first = app.process_sla_checks(NOW)
    second = app.process_sla_checks(NOW)
    assert first["newEscalations"] == 0 and first["notificationEmailsSent"] == 1
    assert second["notificationEmailsSent"] == 0


def test_officer_lookup_cached_within_sweep(aws):
    for i in range(5):
        seed(aws["table"], f"CT-CACHE0000{i}", hours_ago=60)
    app.process_sla_checks(NOW)
    assert aws["calls"]["list_group_users"] == 1  # one Officer lookup for five complaints


def test_resolving_after_breach_keeps_escalation_flag(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    aws["table"].update_item(Key={"complaintId": cid}, UpdateExpression="SET createdAt = :c",
                             ExpressionAttributeValues={":c": (NOW - timedelta(hours=60)).isoformat()})
    app.process_sla_checks(NOW)
    assert get(aws["table"], cid)["escalated"] is True


# ---------------- notifications ----------------

def test_send_notification_filters_invalid_addresses(aws):
    before = sent_count(aws["ses"])
    assert app.send_notification(["not-an-email", "", None, "ok@civictrack.test", "ok@civictrack.test"], "s", "b") is True
    assert sent_count(aws["ses"]) - before == 1  # deduplicated, invalid skipped


def test_send_notification_no_recipients(aws):
    assert app.send_notification(["bad"], "s", "b") is False


def test_send_notification_survives_ses_rejection(aws, monkeypatch):
    monkeypatch.setattr(app, "SES_FROM_EMAIL", "unverified@civictrack.test")
    assert app.send_notification(["x@civictrack.test"], "s", "b") is False


def test_create_records_citizen_notification(aws, make_complaint):
    c = make_complaint()
    item = get(aws["table"], c["complaintId"])
    assert item["lastCitizenNotification"]["kind"] == "created" and item["lastCitizenNotification"]["sent"] is True


def test_email_failure_never_blocks_complaint(aws, monkeypatch, make_complaint):
    monkeypatch.setattr(app, "send_notification", lambda *a, **k: False)
    c = make_complaint()
    assert get(aws["table"], c["complaintId"])["lastCitizenNotification"]["sent"] is False


def test_logs_do_not_contain_recipient_addresses(aws, capsys):
    app.send_notification(["private.person@civictrack.test"], "s", "b")
    assert "private.person" not in capsys.readouterr().out
