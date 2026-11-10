"""Geospatial helpers: distances, GeoJSON validation and point-in-polygon.

Boundaries are only ever loaded from GeoJSON supplied by an administrator.
Nothing here contains or invents administrative boundaries.
"""

import json
import math

EARTH_RADIUS_M = 6371000.0
MAX_BOUNDARY_BYTES = 300 * 1024  # keeps a ward item well under DynamoDB's 400 KB limit
MAX_VERTICES = 20000


def haversine_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


class GeometryError(ValueError):
    pass


def _check_ring(ring):
    if not isinstance(ring, list) or len(ring) < 4:
        raise GeometryError("each polygon ring needs at least 4 positions")
    for position in ring:
        if not (isinstance(position, list) and len(position) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in position[:2])):
            raise GeometryError("positions must be [longitude, latitude] numbers")
        lng, lat = position[0], position[1]
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise GeometryError("coordinate out of range (expected [longitude, latitude])")
    if ring[0][:2] != ring[-1][:2]:
        raise GeometryError("polygon rings must be closed (first position equals last)")
    return len(ring)


def validate_geometry(geometry):
    """Validate a GeoJSON Polygon or MultiPolygon. Returns (geometry, bbox, vertex_count)."""
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise GeometryError("boundary must be a GeoJSON Polygon or MultiPolygon geometry")
    coords = geometry.get("coordinates")
    polygons = [coords] if geometry["type"] == "Polygon" else coords
    if not isinstance(polygons, list) or not polygons:
        raise GeometryError("boundary has no coordinates")
    vertices = 0
    lngs, lats = [], []
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise GeometryError("invalid polygon")
        for ring in polygon:
            vertices += _check_ring(ring)
            lngs.extend(p[0] for p in ring)
            lats.extend(p[1] for p in ring)
    if vertices > MAX_VERTICES:
        raise GeometryError(f"boundary has {vertices} vertices; simplify it below {MAX_VERTICES}")
    clean = {"type": geometry["type"], "coordinates": coords}
    if len(json.dumps(clean, separators=(",", ":"))) > MAX_BOUNDARY_BYTES:
        raise GeometryError("boundary is larger than 300 KB; simplify it before importing")
    return clean, [min(lngs), min(lats), max(lngs), max(lats)], vertices


def _point_in_ring(lng, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lng, lat, geometry, bbox=None):
    """Even-odd ray casting; holes (inner rings) are respected."""
    if bbox and not (bbox[0] <= lng <= bbox[2] and bbox[1] <= lat <= bbox[3]):
        return False
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        if _point_in_ring(lng, lat, polygon[0]) and not any(_point_in_ring(lng, lat, hole) for hole in polygon[1:]):
            return True
    return False


def locate(lng, lat, areas):
    """Return the first area whose boundary contains the point.
    ``areas`` items need ``boundary`` (geometry dict) and optional ``bbox``."""
    for area in areas:
        boundary = area.get("boundary")
        if boundary and point_in_geometry(lng, lat, boundary, area.get("bbox")):
            return area
    return None


def grid_hotspots(points, cell_deg=0.005, min_count=3):
    """Group real complaint coordinates into ~500 m cells; return dense cells."""
    cells = {}
    for p in points:
        key = (math.floor(p["lat"] / cell_deg), math.floor(p["lng"] / cell_deg))
        cell = cells.setdefault(key, {"count": 0, "lat": 0.0, "lng": 0.0, "categories": {}})
        cell["count"] += 1
        cell["lat"] += p["lat"]
        cell["lng"] += p["lng"]
        cell["categories"][p.get("category") or "Other"] = cell["categories"].get(p.get("category") or "Other", 0) + 1
    hotspots = []
    for cell in cells.values():
        if cell["count"] >= min_count:
            top = max(cell["categories"].items(), key=lambda kv: kv[1])[0]
            hotspots.append({"latitude": round(cell["lat"] / cell["count"], 6), "longitude": round(cell["lng"] / cell["count"], 6),
                             "count": cell["count"], "topCategory": top})
    return sorted(hotspots, key=lambda h: h["count"], reverse=True)
