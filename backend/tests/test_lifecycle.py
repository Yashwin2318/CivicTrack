from datetime import datetime, timedelta, timezone

import pytest

import lifecycle as lc

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("item,expected", [
    ({"status": "PENDING"}, "PENDING"),
    ({"status": "PENDING", "assignedOfficerSub": ""}, "PENDING"),
    ({"status": "PENDING", "assignedOfficerSub": "o1"}, "ASSIGNED"),  # legacy record
    ({"status": "IN_PROGRESS", "assignedOfficerSub": "o1"}, "IN_PROGRESS"),
    ({"status": "RESOLVED"}, "RESOLVED"),
    ({}, "PENDING"),
])
def test_effective_status_maps_legacy_records(item, expected):
    assert lc.effective_status(item) == expected


@pytest.mark.parametrize("action,status,allowed", [
    ("assign", "PENDING", True), ("assign", "REOPENED", True), ("assign", "IN_PROGRESS", False),
    ("reassign", "ASSIGNED", True), ("reassign", "PENDING", False),
    ("unassign", "ASSIGNED", True), ("unassign", "IN_PROGRESS", False),
    ("start", "ASSIGNED", True), ("start", "REOPENED", True), ("start", "PENDING", False), ("start", "RESOLVED", False),
    ("resolve", "IN_PROGRESS", True), ("resolve", "ASSIGNED", False),
    ("reopen", "RESOLVED", True), ("reopen", "CLOSED", False), ("reopen", "IN_PROGRESS", False),
    ("close", "RESOLVED", True), ("close", "PENDING", False),
    ("bogus", "PENDING", False),
])
def test_transition_table(action, status, allowed):
    assert lc.can_transition(action, status) is allowed


def test_department_derived_from_category_unless_stored():
    assert lc.department_for({"category": "Roads"}) == "Public Works"
    assert lc.department_for({"category": "Unknown"}) == "General Administration"
    assert lc.department_for({"category": "Roads", "department": "Electrical"}) == "Electrical"


@pytest.mark.parametrize("value,ok", [
    ("2026-11-09T10:00:00+00:00", True), ("2026-11-09T10:00:00Z", True),
    ("2026-11-09T10:00:00", True), ("not-a-date", False), ("", False), (None, False), (123, False),
])
def test_parse_iso_datetime(value, ok):
    assert (lc.parse_iso_datetime(value) is not None) is ok


def test_naive_timestamp_is_utc():
    assert lc.parse_iso_datetime("2026-11-09T10:00:00") == datetime(2026, 11, 9, 10, tzinfo=timezone.utc)


def test_ist_offset_is_normalised_to_utc():
    assert lc.parse_iso_datetime("2026-11-09T15:30:00+05:30") == datetime(2026, 11, 9, 10, tzinfo=timezone.utc)


def _created(hours_ago, **extra):
    return {"status": "ASSIGNED", "createdAt": (NOW - timedelta(hours=hours_ago)).isoformat(), **extra}


@pytest.mark.parametrize("hours_ago,state", [(1, "on_track"), (43.9, "on_track"), (44, "warning"), (47.9, "warning"), (48, "breached"), (100, "breached")])
def test_sla_state_windows(hours_ago, state):
    assert lc.sla_state(_created(hours_ago), NOW, 4)["state"] == state


def test_sla_state_resolved_met_and_missed():
    created = NOW - timedelta(hours=60)
    met = {"status": "RESOLVED", "createdAt": created.isoformat(), "resolvedAt": (created + timedelta(hours=10)).isoformat()}
    missed = {"status": "RESOLVED", "createdAt": created.isoformat(), "resolvedAt": (created + timedelta(hours=50)).isoformat()}
    assert lc.sla_state(met, NOW)["state"] == "met"
    assert lc.sla_state(missed, NOW)["state"] == "missed"


def test_sla_restarts_from_reopen():
    item = _created(100, slaStartedAt=(NOW - timedelta(hours=1)).isoformat(), status="REOPENED")
    assert lc.sla_state(item, NOW)["state"] == "on_track"


def test_sla_unknown_and_bad_hours():
    assert lc.sla_state({"status": "PENDING", "createdAt": "garbage"}, NOW)["state"] == "unknown"
    assert lc.sla_hours_of({"slaHours": "abc"}) == 48
    assert lc.sla_hours_of({"slaHours": 0}) == 1


def test_priority_normalisation():
    assert lc.normalize_priority("high") == "HIGH"
    assert lc.normalize_priority("urgent") is None
    assert lc.normalize_priority(None) is None
