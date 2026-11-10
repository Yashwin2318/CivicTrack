"""Pure-function tests: rule-based intelligence, GeoJSON handling, analytics engine."""

from datetime import datetime, timedelta, timezone

import pytest

import analytics_engine
import geo
import intelligence

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
SQUARE = {"type": "Polygon", "coordinates": [[[77.60, 12.96], [77.62, 12.96], [77.62, 12.98], [77.60, 12.98], [77.60, 12.96]]]}


@pytest.mark.parametrize("title,description,expected", [
    ("Huge pothole on main road", "", "Roads"),
    ("Garbage not collected", "Overflowing trash bins for days", "Sanitation"),
    ("Street light not working", "The lamp near the park is dark", "Streetlights"),
    ("Road flooded after rain", "Stagnant rainwater everywhere", "Waterlogging"),
    ("No water supply since morning", "pipeline leak", "Water Supply"),
])
def test_suggest_category(title, description, expected):
    assert intelligence.suggest_category(title, description)["category"] == expected


def test_suggest_category_no_match():
    assert intelligence.suggest_category("Hello", "something unrelated") == {"category": None, "confidence": 0.0, "matched": []}


def test_priority_score_is_transparent_and_bounded():
    low = intelligence.score_priority("Other", "Paint faded", "")
    high = intelligence.score_priority("Streetlights", "Live wire sparking near school", "children walk here, accident risk",
                                       nearby_open=3, citizen_urgency="HIGH")
    assert low["priority"] == "LOW" and 0 <= low["score"] < 25
    assert high["priority"] == "CRITICAL" and high["score"] == 100
    assert any("electrical hazard" in r for r in high["reasons"]) and any("similar open complaint" in r for r in high["reasons"])


def test_summary_is_extractive_and_capped():
    s = intelligence.summarize("Pothole", "Big hole near gate. It has been there for weeks.")
    assert s == "Pothole: Big hole near gate."
    assert len(intelligence.summarize("T", "x" * 500)) == 160


def test_point_in_polygon_with_hole_and_bbox():
    _, bbox, _ = geo.validate_geometry(SQUARE)
    assert geo.point_in_geometry(77.61, 12.97, SQUARE, bbox)
    assert not geo.point_in_geometry(77.63, 12.97, SQUARE, bbox)
    holed = {"type": "Polygon", "coordinates": SQUARE["coordinates"] + [[[77.605, 12.965], [77.615, 12.965], [77.615, 12.975], [77.605, 12.975], [77.605, 12.965]]]}
    assert not geo.point_in_geometry(77.61, 12.97, holed)
    multi = {"type": "MultiPolygon", "coordinates": [SQUARE["coordinates"]]}
    assert geo.locate(77.61, 12.97, [{"id": "w1", "boundary": multi}])["id"] == "w1"


@pytest.mark.parametrize("geometry,message", [
    ({"type": "Point", "coordinates": [77, 12]}, "Polygon or MultiPolygon"),
    ({"type": "Polygon", "coordinates": [[[77, 12], [78, 12], [77, 12]]]}, "at least 4"),
    ({"type": "Polygon", "coordinates": [[[77, 12], [78, 12], [78, 13], [77, 13.5]]]}, "closed"),
    ({"type": "Polygon", "coordinates": [[[200, 12], [78, 12], [78, 13], [200, 12]]]}, "out of range"),
    ({"type": "Polygon", "coordinates": [[["a", 12], [78, 12], [78, 13], ["a", 12]]]}, "numbers"),
])
def test_invalid_geometry_rejected(geometry, message):
    with pytest.raises(geo.GeometryError, match=message):
        geo.validate_geometry(geometry)


def test_haversine_and_hotspots():
    assert geo.haversine_m(12.9716, 77.5946, 12.9716, 77.5946) == 0
    assert 1100 < geo.haversine_m(12.97, 77.59, 12.98, 77.59) < 1120
    points = [{"lat": 12.9701, "lng": 77.5901, "category": "Roads"}] * 3 + [{"lat": 13.1, "lng": 77.7, "category": "Other"}]
    hotspots = geo.grid_hotspots(points)
    assert len(hotspots) == 1 and hotspots[0]["count"] == 3 and hotspots[0]["topCategory"] == "Roads"


def _item(cid, hours_ago, status="PENDING", **extra):
    return {"complaintId": cid, "status": status, "category": "Roads", "createdAt": (NOW - timedelta(hours=hours_ago)).isoformat(), "slaHours": 48, **extra}


def test_analytics_engine_aggregates_real_fields():
    items = [
        _item("CT-A1", 2, zone="East", ward="W1", latitude=12.97, longitude=77.59),
        _item("CT-A2", 60, "IN_PROGRESS", assignedOfficerSub="o1", assignedOfficerName="One", zone="East"),
        _item("CT-A3", 100, "RESOLVED", resolvedAt=(NOW - timedelta(hours=90)).isoformat(), assignedOfficerName="One"),
        _item("CT-A4", 200, "CLOSED", resolvedAt=(NOW - timedelta(hours=140)).isoformat(), reopenCount=1),
        _item("CT-A5", 5, "REJECTED"),
    ]
    out = analytics_engine.aggregate(items, NOW, {}, "day")
    s = out["summary"]
    assert (s["total"], s["open"], s["resolved"], s["rejected"], s["overdue"]) == (5, 2, 2, 1, 1)
    assert s["avgResolutionHours"] == 35.0 and s["medianResolutionHours"] == 35.0
    assert s["slaComplianceRate"] == 0.5 and s["resolutionRate"] == 0.5 and s["reopenedEver"] == 1
    assert {r["key"]: r["total"] for r in out["byOfficer"]} == {"One": 2}
    assert out["mapped"] == 1 and sum(t["created"] for t in out["trend"]) == 5
    east = analytics_engine.aggregate(items, NOW, {"zone": "East"})
    assert east["summary"]["total"] == 2
    dated = analytics_engine.aggregate(items, NOW, {"from": NOW.date().isoformat()})
    assert dated["summary"]["total"] == 2
