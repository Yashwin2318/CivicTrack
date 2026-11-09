
import json
import unittest
from unittest.mock import MagicMock, patch

import app


class CivicTrackAuthorizationTests(unittest.TestCase):

    def setUp(self):
        self.table = MagicMock()
        self.table.scan.return_value = {"Items": []}

        self.patcher = patch("app.get_table", return_value=self.table)
        self.mock_get_table = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def make_event(
        self,
        method="GET",
        path="/complaints",
        role="Citizen",
        sub="citizen-123",
        body=None,
        authenticated=True
    ):
        event = {
            "httpMethod": method,
            "path": path,
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {}
        }

        if authenticated:
            event["requestContext"]["authorizer"] = {
                "claims": {
                    "sub": sub,
                    "email": f"{sub}@example.com",
                    "cognito:groups": [role]
                }
            }

        return event

    def assert_filter_contains(self, expression, expected_attribute, expected_value):
        expression_details = expression.get_expression()
        values = expression_details["values"]

        attribute = values[0]
        actual_value = values[1]

        self.assertEqual(attribute.name, expected_attribute)
        self.assertEqual(actual_value, expected_value)

    def test_missing_authentication_returns_401(self):
        event = self.make_event(authenticated=False)

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 401)

    def test_unassigned_user_returns_403(self):
        event = self.make_event(role="UnknownRole")

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)

    def test_citizen_can_create_complaint(self):
        body = {
            "title": "Garbage not collected",
            "category": "Sanitation",
            "location": "Bengaluru",
            "description": "Garbage has not been collected for two days."
        }

        event = self.make_event(
            method="POST",
            body=body,
            role="Citizen",
            sub="citizen-123"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 201)
        self.table.put_item.assert_called_once()

        saved_item = self.table.put_item.call_args.kwargs["Item"]

        self.assertEqual(saved_item["citizenSub"], "citizen-123")

    def test_citizen_cannot_impersonate_another_user(self):
        body = {
            "title": "Road damaged",
            "category": "Roads",
            "location": "Bengaluru",
            "description": "Road has multiple potholes.",
            "citizenSub": "victim-456",
            "citizenEmail": "victim@example.com"
        }

        event = self.make_event(
            method="POST",
            body=body,
            role="Citizen",
            sub="attacker-123"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 201)
        self.table.put_item.assert_called_once()

        saved_item = self.table.put_item.call_args.kwargs["Item"]

        self.assertEqual(saved_item["citizenSub"], "attacker-123")
        self.assertNotEqual(saved_item["citizenSub"], "victim-456")
        self.assertEqual(
            saved_item["citizenEmail"],
            "attacker-123@example.com"
        )

    def test_officer_cannot_create_complaint(self):
        body = {
            "title": "Test complaint",
            "category": "Roads",
            "location": "Bengaluru",
            "description": "Testing officer permissions."
        }

        event = self.make_event(
            method="POST",
            body=body,
            role="Officer"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 403)
        self.table.put_item.assert_not_called()

    def test_citizen_only_sees_own_complaints(self):
        event = self.make_event(
            role="Citizen",
            sub="citizen-123"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.table.scan.assert_called_once()

        scan_args = self.table.scan.call_args.kwargs

        self.assertIn("FilterExpression", scan_args)

        self.assert_filter_contains(
            scan_args["FilterExpression"],
            "citizenSub",
            "citizen-123"
        )

    def test_officer_only_sees_assigned_complaints(self):
        event = self.make_event(
            role="Officer",
            sub="officer-123"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.table.scan.assert_called_once()

        scan_args = self.table.scan.call_args.kwargs

        self.assertIn("FilterExpression", scan_args)

        self.assert_filter_contains(
            scan_args["FilterExpression"],
            "assignedOfficerSub",
            "officer-123"
        )

    def test_admin_can_view_all_complaints(self):
        event = self.make_event(
            role="Admin",
            sub="admin-123"
        )

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)
        self.table.scan.assert_called_once()

        scan_args = self.table.scan.call_args.kwargs

        self.assertNotIn("FilterExpression", scan_args)

    def test_health_endpoint_is_public(self):
        event = {
            "httpMethod": "GET",
            "path": "/health",
            "requestContext": {}
        }

        result = app.lambda_handler(event, None)

        self.assertEqual(result["statusCode"], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
