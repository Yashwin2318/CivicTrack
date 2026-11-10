"""Reference data, GeoJSON boundaries, settings, audit log, officer profiles and
Cognito user administration — including authorization of every admin route."""


import pytest

from conftest import call

SQUARE = {"type": "Polygon", "coordinates": [[[77.60, 12.96], [77.62, 12.96], [77.62, 12.98], [77.60, 12.98], [77.60, 12.96]]]}


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/admin/departments/roads", {"name": "Roads"}),
    ("PUT", "/admin/categories/Roads", {"departmentId": "public-works"}),
    ("PUT", "/admin/zones/east", {"name": "East"}),
    ("PUT", "/admin/wards/w1", {"name": "W1"}),
    ("POST", "/admin/boundaries/import", {"level": "ward"}),
    ("PUT", "/admin/settings", {"slaHours": 24}),
    ("GET", "/admin/audit", None),
    ("GET", "/admin/users", None),
    ("POST", "/admin/users", {"email": "x@y.z", "name": "X", "role": "Admin"}),
    ("PATCH", "/admin/users/someone", {"role": "Admin"}),
    ("PUT", "/admin/officers/officer-1/profile", {"available": False}),
    ("POST", "/admin/reference/initialize", None),
    ("GET", "/analytics", None),
])
@pytest.mark.parametrize("role", ["Citizen", "Officer"])
def test_admin_routes_reject_non_admins(aws, role, method, path, body):
    status, data = call(method, path, role, body=body)
    assert status == 403 and data["code"] == "FORBIDDEN"


def test_reference_defaults_then_managed(aws):
    status, ref = call("GET", "/reference", "Citizen")
    assert status == 200 and ref["source"] == "builtin" and len(ref["departments"]) == 6
    assert {c["name"] for c in ref["categories"]} >= {"Roads", "Sanitation", "Other"}
    # First edit persists all built-in departments, then adds the new one.
    assert call("PUT", "/admin/departments/parks", "Admin", body={"name": "Parks & Gardens", "description": "Parks"})[0] == 200
    ref = call("GET", "/reference", "Officer")[1]
    assert ref["source"] == "stored" and len(ref["departments"]) == 7
    assert call("PUT", "/admin/departments/dup", "Admin", body={"name": "parks & gardens"})[0] == 409
    assert call("PUT", "/admin/departments/BAD ID", "Admin", body={"name": "x"})[0] == 400


def test_custom_category_routes_new_complaints(aws, make_complaint):
    call("PUT", "/admin/departments/parks", "Admin", body={"name": "Parks"})
    assert call("PUT", "/admin/categories/Trees", "Admin", body={"departmentId": "parks"})[0] == 200
    assert call("PUT", "/admin/categories/Trees", "Admin", body={"departmentId": "nope"})[0] == 400
    complaint = make_complaint(category="Trees", title="Fallen tree")
    assert (complaint["department"], complaint["departmentId"]) == ("Parks", "parks")
    status, data = call("POST", "/complaints", "Citizen", body={"title": "t", "category": "Unknown cat", "location": "l", "description": "d"})
    assert status == 400 and data["error"] == "Unknown category"


def test_zone_ward_boundaries_drive_auto_location(aws, make_complaint):
    assert call("PUT", "/admin/zones/east", "Admin", body={"name": "East"})[0] == 200
    status, data = call("PUT", "/admin/wards/indiranagar", "Admin", body={"name": "Indiranagar", "number": "80", "zoneId": "east", "boundary": SQUARE, "boundarySource": "test"})
    assert status == 200 and data["ward"]["hasBoundary"] is True
    inside = make_complaint(latitude=12.97, longitude=77.61)
    outside = make_complaint(latitude=13.20, longitude=77.80)
    assert (inside["wardId"], inside["ward"], inside["zoneId"], inside["zone"]) == ("indiranagar", "Indiranagar", "east", "East")
    assert "Located automatically in Indiranagar" in inside["history"][0]["note"]
    assert "wardId" not in outside
    public = call("GET", "/reference", "Citizen")[1]
    assert "boundary" not in public["wards"][0] and public["wards"][0]["hasBoundary"] is True
    with_geo = call("GET", "/reference", "Citizen", query={"boundaries": "1"})[1]
    assert with_geo["wards"][0]["boundary"]["type"] == "Polygon"


def test_ward_validation(aws):
    assert call("PUT", "/admin/wards/w1", "Admin", body={"name": "W1", "zoneId": "missing"})[1]["error"] == "Unknown zone"
    bad = {"type": "Polygon", "coordinates": [[[77, 12], [78, 12], [77, 12]]]}
    assert call("PUT", "/admin/wards/w1", "Admin", body={"name": "W1", "boundary": bad})[0] == 400
    assert call("PUT", "/admin/wards/w1", "Admin", body={"name": "<script>"})[0] == 400


def test_geojson_import_reports_skips(aws):
    call("PUT", "/admin/zones/east", "Admin", body={"name": "East"})
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"WARD_NAME": "Ward A", "WARD_NO": 1, "ZONE": "East"}, "geometry": SQUARE},
        {"type": "Feature", "properties": {"WARD_NAME": "Ward B"}, "geometry": {"type": "Point", "coordinates": [77, 12]}},
        {"type": "Feature", "properties": {}, "geometry": SQUARE},
    ]}
    status, data = call("POST", "/admin/boundaries/import", "Admin", body={
        "level": "ward", "featureCollection": fc, "nameProperty": "WARD_NAME", "numberProperty": "WARD_NO", "zoneProperty": "ZONE", "source": "unit test"})
    assert status == 200 and [i["name"] for i in data["imported"]] == ["Ward A"] and len(data["skipped"]) == 2
    ward = call("GET", "/reference", "Admin")[1]["wards"][0]
    assert (ward["id"], ward["number"], ward["zoneId"]) == ("ward-a", "1", "east")
    assert call("POST", "/admin/boundaries/import", "Admin", body={"level": "district", "featureCollection": fc})[0] == 400
    assert call("POST", "/admin/boundaries/import", "Admin", body={"level": "ward", "featureCollection": {"type": "Feature"}})[0] == 400


def test_settings_change_sla_for_new_complaints(aws, make_complaint):
    assert call("PUT", "/admin/settings", "Admin", body={"slaHours": 24, "warningHours": 2})[0] == 200
    assert make_complaint()["slaHours"] == 24
    assert call("PUT", "/admin/settings", "Admin", body={"warningHours": 30})[0] == 400  # >= slaHours
    assert call("PUT", "/admin/settings", "Admin", body={"slaHours": 0})[0] == 400
    assert call("PUT", "/admin/settings", "Admin", body={"discloseOfficerName": "no"})[0] == 400


def test_officer_name_disclosure_setting(aws, make_complaint):
    cid = make_complaint()["complaintId"]
    call("PATCH", f"/complaints/{cid}/assign", "Admin", body={"officerSub": "officer-1"})
    call("PUT", "/admin/settings", "Admin", body={"discloseOfficerName": False})
    data = call("GET", f"/complaints/{cid}", "Citizen", "citizen-1")[1]["complaint"]
    assert "assignedOfficerName" not in data and all("officerName" not in h for h in data["history"])
    assert call("GET", f"/complaints/{cid}", "Admin")[1]["complaint"]["assignedOfficerName"] == "Officer One"


def test_audit_log_records_admin_actions(aws):
    call("PUT", "/admin/zones/east", "Admin", "admin-9", body={"name": "East"})
    call("PUT", "/admin/settings", "Admin", "admin-9", body={"slaHours": 36})
    status, data = call("GET", "/admin/audit", "Admin")
    assert status == 200
    actions = [e["action"] for e in data["entries"]]
    assert "zone_saved" in actions and "settings_updated" in actions
    assert all(e["actorSub"] == "admin-9" for e in data["entries"])
    assert call("GET", "/admin/audit", "Admin", query={"month": "2026-13"})[0] == 400


def test_officer_profile_and_availability(aws, make_complaint):
    call("PUT", "/admin/zones/east", "Admin", body={"name": "East"})
    status, data = call("PUT", "/admin/officers/officer-1/profile", "Admin",
                        body={"departmentId": "public-works", "zoneIds": ["east"], "available": False, "maxActive": 5})
    assert status == 200 and data["profile"]["zoneIds"] == ["east"]
    assert call("PUT", "/admin/officers/officer-1/profile", "Admin", body={"zoneIds": ["nope"]})[0] == 400
    assert call("PUT", "/admin/officers/ghost/profile", "Admin", body={"available": True})[0] == 404
    officers = {o["sub"]: o for o in call("GET", "/officers", "Admin")[1]["officers"]}
    assert officers["officer-1"]["profile"]["available"] is False
    cid = make_complaint()["complaintId"]
    status, data = call("PATCH", f"/complaints/{cid}/assign", "Admin", body={"officerSub": "officer-1"})
    assert status == 409 and "unavailable" in data["error"]
    assert call("PATCH", f"/complaints/{cid}/assign", "Admin", body={"officerSub": "officer-1", "force": True})[0] == 200
    me = call("GET", "/me", "Officer", "officer-1")[1]["user"]
    assert me["profile"]["departmentName"] == "Public Works" and me["profile"]["zoneNames"] == ["East"]


def test_admin_metadata_with_managed_ids(aws, make_complaint):
    call("PUT", "/admin/zones/east", "Admin", body={"name": "East"})
    call("PUT", "/admin/wards/w80", "Admin", body={"name": "Ward 80", "zoneId": "east"})
    cid = make_complaint()["complaintId"]
    status, data = call("PATCH", f"/complaints/{cid}", "Admin", body={"wardId": "w80", "departmentId": "electrical"})
    c = data["complaint"]
    assert status == 200 and (c["ward"], c["zone"], c["department"]) == ("Ward 80", "East", "Electrical")
    assert call("PATCH", f"/complaints/{cid}", "Admin", body={"wardId": "nope"})[0] == 400


# ---------------- Cognito user administration (moto Cognito) ----------------

def test_user_admin_create_list_role_and_disable(cognito):
    admin_sub = cognito["add_user"]("boss@civictrack.test", "Admin", "Boss")
    status, data = call("POST", "/admin/users", "Admin", admin_sub, {"email": "New.Officer@civictrack.test", "name": "New Officer", "role": "Officer"})
    assert status == 201 and data["user"]["role"] == "Officer" and data["user"]["email"] == "new.officer@civictrack.test"
    assert call("POST", "/admin/users", "Admin", admin_sub, {"email": "new.officer@civictrack.test", "name": "Dup", "role": "Officer"})[0] == 409
    assert call("POST", "/admin/users", "Admin", admin_sub, {"email": "c@x.io", "name": "C", "role": "Citizen"})[0] == 400
    assert call("POST", "/admin/users", "Admin", admin_sub, {"email": "not-an-email", "name": "C", "role": "Officer"})[0] == 400

    users = call("GET", "/admin/users", "Admin", admin_sub, query={"role": "Officer"})[1]["users"]
    username = users[0]["username"]
    assert [u["email"] for u in users] == ["new.officer@civictrack.test"]
    # The officer now appears in the assignment directory.
    assert [o["email"] for o in call("GET", "/officers", "Admin", admin_sub)[1]["officers"]] == ["new.officer@civictrack.test"]

    assert call("PATCH", f"/admin/users/{username}", "Admin", admin_sub, {"role": "Admin"})[0] == 200
    groups = [g["GroupName"] for g in cognito["client"].admin_list_groups_for_user(UserPoolId=cognito["pool_id"], Username=username)["Groups"]]
    assert groups == ["Admin"]
    assert call("PATCH", f"/admin/users/{username}", "Admin", admin_sub, {"enabled": False})[0] == 200
    assert cognito["client"].admin_get_user(UserPoolId=cognito["pool_id"], Username=username)["Enabled"] is False
    assert call("PATCH", "/admin/users/missing-user", "Admin", admin_sub, {"enabled": False})[0] == 404
    assert call("PATCH", f"/admin/users/{username}", "Admin", admin_sub, {"role": "Superuser"})[0] == 400


def test_admin_cannot_change_own_access(cognito):
    admin_sub = cognito["add_user"]("self@civictrack.test", "Admin", "Self")
    status, data = call("PATCH", "/admin/users/self@civictrack.test", "Admin", admin_sub, {"role": "Citizen"})
    assert status == 409 and "own" in data["error"]


def test_unassigned_role_cannot_reach_admin(cognito):
    """Privilege escalation attempt: a forged body cannot grant a role; role comes only from token groups."""
    status, _ = call("POST", "/admin/users", "Citizen", body={"email": "a@b.co", "name": "A", "role": "Admin", "cognito:groups": ["Admin"]})
    assert status == 403
