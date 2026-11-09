"""Pure complaint-lifecycle rules for CivicTrack.

This module has no AWS dependencies so it can be unit-tested directly and
mirrored by the frontend (frontend/src/lib/lifecycle.js).

Backward compatibility
----------------------
Records created before the lifecycle extension use only ``PENDING``,
``IN_PROGRESS`` and ``RESOLVED``. A legacy record whose status is ``PENDING``
but which already has an ``assignedOfficerSub`` is treated as ``ASSIGNED``.
No stored data needs to be migrated.
"""

from datetime import datetime, timedelta, timezone

PENDING = "PENDING"
ASSIGNED = "ASSIGNED"
IN_PROGRESS = "IN_PROGRESS"
RESOLVED = "RESOLVED"
REOPENED = "REOPENED"
CLOSED = "CLOSED"

ALL_STATUSES = (PENDING, ASSIGNED, IN_PROGRESS, RESOLVED, REOPENED, CLOSED)

# Statuses for which the SLA clock is running.
OPEN_STATUSES = frozenset({PENDING, ASSIGNED, IN_PROGRESS, REOPENED})
FINAL_STATUSES = frozenset({RESOLVED, CLOSED})

# action -> (allowed effective statuses before, status after)
TRANSITIONS = {
    "assign": (frozenset({PENDING, REOPENED}), ASSIGNED),
    "reassign": (frozenset({ASSIGNED}), ASSIGNED),
    "unassign": (frozenset({ASSIGNED}), PENDING),
    "start": (frozenset({ASSIGNED, REOPENED}), IN_PROGRESS),
    "resolve": (frozenset({IN_PROGRESS}), RESOLVED),
    "reopen": (frozenset({RESOLVED}), REOPENED),
    "close": (frozenset({RESOLVED}), CLOSED),
}

PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# CivicTrack's own complaint categories mapped to the department that owns them.
# These are application categories, not administrative boundaries.
CATEGORY_DEPARTMENTS = {
    "Roads": "Public Works",
    "Sanitation": "Solid Waste Management",
    "Streetlights": "Electrical",
    "Waterlogging": "Storm Water Drains",
    "Water Supply": "Water Supply",
    "Other": "General Administration",
}
DEPARTMENTS = tuple(sorted(set(CATEGORY_DEPARTMENTS.values())))
DEFAULT_SLA_HOURS = 48


def effective_status(item):
    """Return the lifecycle status of a stored complaint, mapping legacy values."""
    status = (item or {}).get("status") or PENDING
    if status == PENDING and (item or {}).get("assignedOfficerSub"):
        return ASSIGNED
    return status


def can_transition(action, current_status):
    rule = TRANSITIONS.get(action)
    return bool(rule) and current_status in rule[0]


def next_status(action):
    return TRANSITIONS[action][1]


def department_for(item):
    """Stored department wins; otherwise derive it from the category."""
    department = (item or {}).get("department")
    if isinstance(department, str) and department.strip():
        return department.strip()
    return CATEGORY_DEPARTMENTS.get((item or {}).get("category"), "General Administration")


def normalize_priority(value):
    if isinstance(value, str) and value.strip().upper() in PRIORITIES:
        return value.strip().upper()
    return None


def parse_iso_datetime(value):
    """Parse ISO-8601; naive values are treated as UTC. Returns None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sla_hours_of(item):
    try:
        return max(1, int((item or {}).get("slaHours", DEFAULT_SLA_HOURS)))
    except (TypeError, ValueError):
        return DEFAULT_SLA_HOURS


def sla_clock_start(item):
    """SLA restarts when a complaint is reopened."""
    return parse_iso_datetime((item or {}).get("slaStartedAt")) or parse_iso_datetime(
        (item or {}).get("createdAt")
    )


def sla_state(item, now, warning_hours=4):
    """Describe the SLA position of a complaint at ``now`` (aware datetime).

    state is one of: unknown, on_track, warning, breached, met, missed.
    """
    start = sla_clock_start(item)
    if not start:
        return {"state": "unknown", "deadline": None, "warningAt": None}

    hours = sla_hours_of(item)
    deadline = start + timedelta(hours=hours)
    warning_at = deadline - timedelta(hours=max(0, min(warning_hours, hours)))
    status = effective_status(item)

    if status in FINAL_STATUSES:
        finished = parse_iso_datetime(item.get("resolvedAt")) or parse_iso_datetime(
            item.get("closedAt")
        )
        state = "met" if finished and finished <= deadline else "missed" if finished else "met"
    elif now >= deadline:
        state = "breached"
    elif now >= warning_at:
        state = "warning"
    else:
        state = "on_track"

    return {
        "state": state,
        "deadline": deadline.isoformat(),
        "warningAt": warning_at.isoformat(),
        "hours": hours,
    }
