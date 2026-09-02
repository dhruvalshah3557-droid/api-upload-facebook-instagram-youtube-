#!/usr/bin/env python3
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog


def _account(account_id, platform, enabled=True, platform_account_id="123"):
    return {
        "account_id": account_id,
        "platform": platform,
        "enabled": enabled,
        "platform_account_id": platform_account_id,
        "timezone": "UTC",
    }


class WatchdogDeliveryTests(unittest.TestCase):
    def test_records_from_matrix_map_headers(self):
        matrix = [
            ["account_id", "platform", "enabled", "platform_account_id"],
            ["FB-CD", "Facebook", "Yes", "1569"],
            ["LINE-CD", "LINE", "No", ""],
        ]
        records, error = watchdog.records_from_matrix(matrix, watchdog.ACCOUNT_HEADERS)
        self.assertIsNone(error)
        self.assertEqual(records[0]["account_id"], "FB-CD")
        self.assertEqual(records[1]["enabled"], "No")

    def test_delivery_deficits_mark_due_accounts_after_four_hours(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        accounts = [
            _account("IG-SPAIN", "instagram"),
            _account("IG-ITALY", "instagram"),
            _account("YT-CD", "youtube"),
            _account("IG-SWEDEN", "instagram", platform_account_id=""),
        ]
        queue_rows = [
            {
                "account_id": "IG-SPAIN",
                "status": "uploaded",
                "last_attempt_at": "2026-08-30 06:00:00",
            },
            {
                "account_id": "IG-ITALY",
                "status": "uploaded",
                "last_attempt_at": "2026-08-30 11:00:00",
            },
            {
                "account_id": "YT-CD",
                "status": "uploaded",
                "last_attempt_at": "2026-08-30 08:00:00",
            },
            {
                "account_id": "YT-CD",
                "status": "uploaded",
                "last_attempt_at": "2026-08-30 03:00:00",
            },
            {
                "account_id": "YT-CD",
                "status": "uploaded",
                "last_attempt_at": "2026-08-29 20:00:00",
            },
            {
                "account_id": "YT-CD",
                "status": "uploaded",
                "last_attempt_at": "2026-08-29 16:00:00",
            },
            {
                "account_id": "YT-CD",
                "status": "uploaded",
                "last_attempt_at": "2026-08-29 13:00:00",
            },
        ]
        activity, due = watchdog.delivery_deficits(accounts, queue_rows, now)
        self.assertEqual(activity["IG-SPAIN"]["count"], 1)
        self.assertEqual(activity["IG-ITALY"]["count"], 1)
        self.assertEqual(activity["YT-CD"]["count"], 5)
        self.assertNotIn("IG-SWEDEN", activity)
        self.assertEqual([item["account_id"] for item in due], ["IG-SPAIN"])
        self.assertEqual(due[0]["deficit"], 4)

    def test_should_dispatch_when_due_and_not_in_flight(self):
        due = [{"account_id": "IG-SPAIN", "deficit": 4}]
        self.assertTrue(watchdog.should_dispatch_production(due, []))
        self.assertFalse(watchdog.should_dispatch_production([], []))
        in_flight = [{"status": "in_progress", "conclusion": None}]
        self.assertTrue(watchdog.production_in_flight(in_flight))
        self.assertFalse(watchdog.should_dispatch_production(due, in_flight))

    def test_dispatch_production_posts_workflow_dispatch(self):
        with patch.object(watchdog, "gh_api", return_value=(204, None)) as api:
            result = watchdog.dispatch_production("token", ref="master")
        self.assertEqual(result, "dispatched Auto Upload Production")
        path = api.call_args.args[2]
        self.assertIn("/actions/workflows/", path)
        self.assertIn("dispatches", path)
        self.assertEqual(api.call_args.args[3], {"ref": "master"})

    def test_queue_status_counts_from_rows(self):
        rows = [
            {"status": "uploaded"},
            {"status": "uploaded"},
            {"status": "pending"},
        ]
        self.assertEqual(
            watchdog.queue_status_counts(rows),
            {"uploaded": 2, "pending": 1},
        )


if __name__ == "__main__":
    unittest.main()
