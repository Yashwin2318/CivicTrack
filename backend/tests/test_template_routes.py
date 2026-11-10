"""Every route the Lambda handles must be wired in API Gateway (both templates),
and every API Gateway route must reach a handler. Prevents 403 'Missing
Authentication Token' surprises after deployment."""

import re
from pathlib import Path

import pytest
from cfnlint.decode import cfn_yaml

import app

ROOT = Path(__file__).resolve().parents[2] / "infrastructure"


def template_routes(name):
    template = cfn_yaml.load(str(ROOT / name))
    function = next(r for r in template["Resources"].values() if r["Type"] == "AWS::Serverless::Function"
                    and "Events" in r["Properties"] and any(e["Type"] == "Api" for e in r["Properties"]["Events"].values()))
    return {(e["Properties"]["Method"].upper(), re.sub(r"\{[^}]+\}", "{}", e["Properties"]["Path"]))
            for e in function["Properties"]["Events"].values() if e["Type"] == "Api"}


def handler_routes():
    routes = {("GET", "/health")}
    for pattern, handlers in app.ROUTES:
        path = pattern.replace(app.ID, "{}")
        group = re.search(r"\(([a-z|]+)\)", path)
        variants = [path.replace(group.group(0), v) for v in group.group(1).split("|")] if group else [path]
        routes |= {(method, p) for p in variants for method in handlers}
    return routes


@pytest.mark.parametrize("template", ["template.yaml", "template.local.yaml"])
def test_routes_match_template(template):
    deployed, handled = template_routes(template), handler_routes()
    if template == "template.local.yaml":
        handled = {r for r in handled if not r[1].startswith("/admin/users")}  # Cognito admin needs a real pool
        deployed = {r for r in deployed if not r[1].startswith("/admin/users")}
    assert handled - deployed == set(), f"handled but not in {template}"
    assert deployed - handled == set(), f"in {template} but no handler"
