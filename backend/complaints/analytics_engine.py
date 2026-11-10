"""Server-side aggregation over stored complaint records (no sample data)."""

from datetime import timedelta
from statistics import median

import geo
import lifecycle as lc


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolution_hours(item):
    created = lc.parse_iso_datetime(item.get("createdAt"))
    resolved = lc.parse_iso_datetime(item.get("resolvedAt"))
    if not created or not resolved or resolved < created:
        return None
    return (resolved - created).total_seconds() / 3600


def _bucket(dt, granularity):
    if granularity == "month":
        return dt.strftime("%Y-%m")
    if granularity == "week":
        monday = dt.date() - timedelta(days=dt.weekday())
        return monday.isoformat()
    return dt.date().isoformat()


def matches(item, filters):
    """Filter on stored attributes. Dates are inclusive UTC days (YYYY-MM-DD)."""
    created = lc.parse_iso_datetime(item.get("createdAt"))
    if filters.get("from") and (not created or created.date().isoformat() < filters["from"]):
        return False
    if filters.get("to") and (not created or created.date().isoformat() > filters["to"]):
        return False
    if filters.get("department") and lc.department_for(item) != filters["department"]:
        return False
    for key in ("zone", "ward", "category"):
        if filters.get(key) and (item.get(key) or "Unspecified") != filters[key]:
            return False
    if filters.get("priority") and (lc.normalize_priority(item.get("priority")) or "MEDIUM") != filters["priority"]:
        return False
    return True


def _group(items, key_fn, now, warning_hours):
    groups = {}
    for item in items:
        groups.setdefault(key_fn(item) or "Unspecified", []).append(item)
    rows = []
    for key, members in groups.items():
        rows.append({"key": key, **_stats(members, now, warning_hours, light=True)})
    return sorted(rows, key=lambda r: (-r["total"], r["key"]))


def _stats(items, now, warning_hours, light=False):
    status_counts = {s: 0 for s in lc.ALL_STATUSES}
    sla_counts = {}
    hours = []
    for item in items:
        status = lc.effective_status(item)
        status_counts[status] = status_counts.get(status, 0) + 1
        state = lc.sla_state(item, now, warning_hours)["state"]
        sla_counts[state] = sla_counts.get(state, 0) + 1
        h = _resolution_hours(item)
        if h is not None:
            hours.append(h)
    total = len(items)
    resolved = status_counts[lc.RESOLVED] + status_counts[lc.CLOSED]
    evaluated = sla_counts.get("met", 0) + sla_counts.get("missed", 0)
    stats = {
        "total": total,
        "open": sum(status_counts[s] for s in lc.OPEN_STATUSES),
        "resolved": resolved,
        "overdue": sla_counts.get("breached", 0),
        "resolutionRate": round(resolved / (total - status_counts[lc.REJECTED]), 4) if total - status_counts[lc.REJECTED] else None,
        "avgResolutionHours": round(sum(hours) / len(hours), 2) if hours else None,
        "slaComplianceRate": round(sla_counts.get("met", 0) / evaluated, 4) if evaluated else None,
    }
    if light:
        return stats
    stats.update({
        "byStatus": status_counts,
        "bySla": sla_counts,
        "rejected": status_counts[lc.REJECTED],
        "warning": sla_counts.get("warning", 0),
        "medianResolutionHours": round(median(hours), 2) if hours else None,
        "slaEvaluated": evaluated,
        "slaMet": sla_counts.get("met", 0),
        "reopenedEver": sum(1 for i in items if int(_num(i.get("reopenCount")) or 0) > 0),
        "escalated": sum(1 for i in items if i.get("escalated") is True),
    })
    return stats


def aggregate(items, now, filters=None, granularity="day", warning_hours=4):
    filters = filters or {}
    scoped = [i for i in items if matches(i, filters)]
    summary = _stats(scoped, now, warning_hours)

    # "New" = created within the requested period, or the last 7 days when no period is set.
    since = filters.get("from") or (now - timedelta(days=7)).date().isoformat()
    summary["newInPeriod"] = sum(1 for i in scoped if (lc.parse_iso_datetime(i.get("createdAt")) or now).date().isoformat() >= since)

    trend = {}
    for item in scoped:
        created = lc.parse_iso_datetime(item.get("createdAt"))
        resolved = lc.parse_iso_datetime(item.get("resolvedAt"))
        if created:
            trend.setdefault(_bucket(created, granularity), {"created": 0, "resolved": 0})["created"] += 1
        if resolved:
            trend.setdefault(_bucket(resolved, granularity), {"created": 0, "resolved": 0})["resolved"] += 1

    points = [{"lat": _num(i.get("latitude")), "lng": _num(i.get("longitude")), "category": i.get("category")}
              for i in scoped if _num(i.get("latitude")) is not None and _num(i.get("longitude")) is not None]

    return {
        "generatedAt": now.isoformat(),
        "filters": filters,
        "granularity": granularity,
        "summary": summary,
        "byDepartment": _group(scoped, lc.department_for, now, warning_hours),
        "byCategory": _group(scoped, lambda i: i.get("category"), now, warning_hours),
        "byPriority": _group(scoped, lambda i: lc.normalize_priority(i.get("priority")) or "MEDIUM", now, warning_hours),
        "byZone": _group(scoped, lambda i: i.get("zone"), now, warning_hours),
        "byWard": _group(scoped, lambda i: i.get("ward"), now, warning_hours),
        "byOfficer": _group([i for i in scoped if i.get("assignedOfficerName")], lambda i: i.get("assignedOfficerName"), now, warning_hours),
        "trend": [{"period": k, **v} for k, v in sorted(trend.items())],
        "hotspots": geo.grid_hotspots(points),
        "mapped": len(points),
    }
