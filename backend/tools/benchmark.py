"""In-process micro-benchmark of CivicTrack handlers against moto (no AWS).

Measures the Python code path (validation, triage, serialization, aggregation)
plus moto's in-memory emulation. It does NOT measure API Gateway, Lambda cold
starts, network latency or real DynamoDB/S3 performance.

    backend/.venv/Scripts/python backend/tools/benchmark.py [N]
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.update({"AWS_REGION": "ap-south-1", "AWS_DEFAULT_REGION": "ap-south-1", "AWS_ACCESS_KEY_ID": "bench",
                   "AWS_SECRET_ACCESS_KEY": "bench", "COMPLAINTS_TABLE": "Bench-Complaints", "CONFIG_TABLE": "Bench-Config",
                   "S3_BUCKET": "bench-evidence", "USER_POOL_ID": "", "SES_FROM_EMAIL": ""})
for var in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SAM_LOCAL", "AWS_ENDPOINT_URL"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "complaints"))

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import app  # noqa: E402


def call(method, path, role, sub, body=None, query=None):
    event = {"httpMethod": method, "path": path, "queryStringParameters": query, "headers": {},
             "requestContext": {"authorizer": {"claims": {"sub": sub, "email": f"{sub}@bench.test", "cognito:groups": role}}}}
    if body is not None:
        event["body"] = json.dumps(body)
    start = time.perf_counter()
    result = app.lambda_handler(event, None)
    return (time.perf_counter() - start) * 1000, result["statusCode"]


def stats(samples):
    s = sorted(samples)
    return {"n": len(s), "median_ms": round(statistics.median(s), 2), "p95_ms": round(s[int(0.95 * (len(s) - 1))], 2), "max_ms": round(s[-1], 2)}


def main(n):
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        ddb.create_table(TableName=app.TABLE_NAME, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "complaintId", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in ("complaintId", "citizenSub", "createdAt")],
                         GlobalSecondaryIndexes=[{"IndexName": app.CITIZEN_INDEX, "Projection": {"ProjectionType": "ALL"},
                                                  "KeySchema": [{"AttributeName": "citizenSub", "KeyType": "HASH"}, {"AttributeName": "createdAt", "KeyType": "RANGE"}]}])
        ddb.create_table(TableName=app.CONFIG_TABLE_NAME, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}])
        app.reference.invalidate()
        app.list_group_users = lambda group: []
        create = []
        for i in range(n):
            body = {"title": f"Pothole number {i}", "category": "Roads", "location": "Bench road",
                    "description": "Synthetic benchmark record.", "latitude": 12.95 + (i % 50) * 0.001, "longitude": 77.60 + (i // 50) * 0.001}
            ms, status = call("POST", "/complaints", "Citizen", f"citizen-{i % 100}", body)
            assert status == 201, status
            create.append(ms)
        list_admin = [call("GET", "/complaints", "Admin", "admin")[0] for _ in range(10)]
        list_citizen = [call("GET", "/complaints", "Citizen", "citizen-1")[0] for _ in range(10)]
        analytics = [call("GET", "/analytics", "Admin", "admin")[0] for _ in range(10)]
        similar = [call("POST", "/complaints/similar", "Citizen", "citizen-1", {"title": "pothole", "category": "Roads", "latitude": 12.96, "longitude": 77.60})[0] for _ in range(10)]
        sweep = []
        for _ in range(5):
            start = time.perf_counter()
            app.process_sla_checks()
            sweep.append((time.perf_counter() - start) * 1000)
        print(json.dumps({"records": n, "environment": "in-process handler + moto 5 (no network, no AWS)",
                          "create_complaint": stats(create), "list_all_admin": stats(list_admin), "list_own_citizen": stats(list_citizen),
                          "analytics_aggregate": stats(analytics), "duplicate_check": stats(similar), "sla_sweep": stats(sweep)}, indent=2))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
