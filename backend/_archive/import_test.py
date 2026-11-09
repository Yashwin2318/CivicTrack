
import time

print("CHECK 1: Diagnostic started", flush=True)

print("CHECK 2: Importing boto3", flush=True)
import boto3
print("CHECK 3: Boto3 imported successfully", flush=True)

print("CHECK 4: Importing CivicTrack app", flush=True)
import app
print("CHECK 5: CivicTrack app imported successfully", flush=True)


def lambda_handler(event, context):
    print("CHECK 6: Handler started", flush=True)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": '{"status":"healthy","message":"Import diagnostic passed"}'
    }
