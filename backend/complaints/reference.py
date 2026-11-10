"""Reference data stored in the CivicTrack-Config table.

Single-table layout (partition key ``pk``, sort key ``sk``):

    DEPT      / <departmentId>   department
    CATEGORY  / <category name>  category -> department routing
    ZONE      / <zoneId>         zone (+ optional GeoJSON boundary)
    WARD      / <wardId>         ward (+ zoneId, optional GeoJSON boundary)
    OFFICER   / <cognito sub>    officer profile (department, zones, wards, availability)
    SETTINGS  / global           SLA hours, warning hours, disclosure flags
    AUDIT#YYYY-MM / <iso>#<id>   administrative audit log

When no departments have been saved yet, the built-in CivicTrack defaults
(the category map in lifecycle.py) are returned with ``source: "builtin"``.
"""

import json
import re
import time
import uuid
from decimal import Decimal

from boto3.dynamodb.conditions import Key

import lifecycle as lc

CACHE_SECONDS = 60
_cache = {"at": 0.0, "value": None}

DEFAULT_SETTINGS = {"slaHours": lc.DEFAULT_SLA_HOURS, "warningHours": 4, "discloseOfficerName": True, "maxComplaintsPerHour": 10}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:48] or uuid.uuid4().hex[:8]


def builtin_departments():
    return [{"id": slugify(name), "name": name, "description": "", "active": True, "builtin": True}
            for name in lc.DEPARTMENTS]


def builtin_categories():
    return [{"name": cat, "departmentId": slugify(dept), "active": True, "builtin": True}
            for cat, dept in lc.CATEGORY_DEPARTMENTS.items()]


def invalidate():
    _cache["at"] = 0.0
    _cache["value"] = None


def _query_all(table, pk):
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(pk)}
    while True:
        result = table.query(**kwargs)
        items.extend(result.get("Items", []))
        if not result.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]


def _clean(item):
    out = {k: v for k, v in item.items() if k not in ("pk", "sk", "boundaryJson", "bboxJson")}
    for k, v in list(out.items()):
        if isinstance(v, Decimal):
            out[k] = int(v) if v == v.to_integral_value() else float(v)
    if item.get("boundaryJson"):
        out["boundary"] = json.loads(item["boundaryJson"])
        out["bbox"] = json.loads(item.get("bboxJson") or "null")
    return out


def load(table, use_cache=True):
    """All reference data, cached briefly per Lambda container."""
    if use_cache and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    departments = [_clean(i) | {"id": i["sk"]} for i in _query_all(table, "DEPT")]
    categories = [_clean(i) | {"name": i["sk"]} for i in _query_all(table, "CATEGORY")]
    zones = [_clean(i) | {"id": i["sk"]} for i in _query_all(table, "ZONE")]
    wards = [_clean(i) | {"id": i["sk"]} for i in _query_all(table, "WARD")]
    officers = {i["sk"]: _clean(i) for i in _query_all(table, "OFFICER")}
    settings_items = [i for i in _query_all(table, "SETTINGS") if i["sk"] == "global"]
    settings = dict(DEFAULT_SETTINGS)
    if settings_items:
        settings.update({k: v for k, v in _clean(settings_items[0]).items() if k in DEFAULT_SETTINGS})
    source = "stored" if departments else "builtin"
    if not departments:
        departments = builtin_departments()
    stored_names = {c["name"] for c in categories}
    categories += [c for c in builtin_categories() if c["name"] not in stored_names]
    value = {
        "departments": sorted(departments, key=lambda d: d["name"].lower()),
        "categories": sorted(categories, key=lambda c: c["name"].lower()),
        "zones": sorted(zones, key=lambda z: z["name"].lower()),
        "wards": sorted(wards, key=lambda w: (str(w.get("number", "")).zfill(6), w["name"].lower())),
        "officers": officers,
        "settings": settings,
        "source": source,
    }
    _cache["value"], _cache["at"] = value, time.time()
    return value


def public_view(ref, include_boundaries=False):
    """Reference data safe for any signed-in user (officer profiles excluded)."""
    def strip(area):
        return area if include_boundaries else {k: v for k, v in area.items() if k not in ("boundary", "bbox")} | {"hasBoundary": bool(area.get("boundary"))}
    return {
        "departments": ref["departments"],
        "categories": ref["categories"],
        "zones": [strip(z) for z in ref["zones"]],
        "wards": [strip(w) for w in ref["wards"]],
        "settings": ref["settings"],
        "source": ref["source"],
    }


def department_by_id(ref, dept_id):
    return next((d for d in ref["departments"] if d["id"] == dept_id), None)


def department_by_name(ref, name):
    return next((d for d in ref["departments"] if d["name"] == name), None)


def route_category(ref, category):
    """Department for a category: stored mapping, else built-in default."""
    mapping = next((c for c in ref["categories"] if c["name"] == category and c.get("active", True)), None)
    dept = department_by_id(ref, mapping["departmentId"]) if mapping else None
    if dept:
        return dept
    name = lc.CATEGORY_DEPARTMENTS.get(category, "General Administration")
    return department_by_name(ref, name) or {"id": slugify(name), "name": name}


def put_item(table, pk, sk, attributes):
    table.put_item(Item={"pk": pk, "sk": sk, **attributes})
    invalidate()


def update_attributes(table, pk, sk, attributes):
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(attributes.items()):
        names[f"#a{i}"], values[f":v{i}"] = k, v
        sets.append(f"#a{i} = :v{i}")
    table.update_item(Key={"pk": pk, "sk": sk}, UpdateExpression="SET " + ", ".join(sets),
                      ExpressionAttributeNames=names, ExpressionAttributeValues=values)
    invalidate()


def boundary_attributes(geometry, bbox):
    return {"boundaryJson": json.dumps(geometry, separators=(",", ":")), "bboxJson": json.dumps(bbox)}


def write_audit(table, user, action, target, details=None, now_iso=None):
    from datetime import datetime, timezone
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    item = {"pk": f"AUDIT#{now_iso[:7]}", "sk": f"{now_iso}#{uuid.uuid4().hex[:8]}", "at": now_iso,
            "actorSub": user["sub"], "actorRole": user["role"], "action": action, "target": target}
    if details:
        item["details"] = json.dumps(details, default=str)[:4000]
    table.put_item(Item=item)


def read_audit(table, month, limit=200):
    result = table.query(KeyConditionExpression=Key("pk").eq(f"AUDIT#{month}"), ScanIndexForward=False, Limit=limit)
    entries = []
    for item in result.get("Items", []):
        entry = _clean(item)
        entry["details"] = json.loads(item["details"]) if item.get("details") else None
        entries.append(entry)
    return entries
