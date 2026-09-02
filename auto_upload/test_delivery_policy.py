#!/usr/bin/env python3
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _install(name, attrs=None):
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    parent = sys.modules.get(".".join(name.split(".")[:-1]))
    if parent is not None:
        setattr(parent, name.split(".")[-1], mod)
    return mod


if "dotenv" not in sys.modules:
    gspread = _install("gspread", {})
    gspread.exceptions = types.SimpleNamespace(APIError=Exception)
    _install("oauth2client")
    _install("oauth2client.service_account", {"ServiceAccountCredentials": lambda *a, **k: None})
    _install("dotenv", {"load_dotenv": lambda *a, **k: None})
    _install("requests", {
        "get": lambda *a, **k: None,
        "post": lambda *a, **k: None,
        "RequestException": Exception,
        "Timeout": type("Timeout", (Exception,), {}),
        "ConnectionError": type("ConnectionError", (Exception,), {}),
    })
    _install("google_auth_oauthlib")
    _install("google_auth_oauthlib.flow", {"InstalledAppFlow": lambda *a, **k: None})
    _install("google.auth")
    _install("google.auth.transport")
    _install("google.auth.transport.requests", {"Request": lambda *a, **k: None})
    _install("google.oauth2")
    _install("google.oauth2.credentials", {"Credentials": lambda *a, **k: None})
    _install("googleapiclient")
    _install("googleapiclient.discovery", {"build": lambda *a, **k: None})
    _install("googleapiclient.http", {"MediaIoBaseUpload": lambda *a, **k: None})

import delivery_policy
import optimized_runner


def _primary(account_id, platform, enabled=True, platform_account_id="123"):
    return {
        "account_id": account_id,
        "platform": platform,
        "enabled": enabled,
        "platform_account_id": platform_account_id,
        "timezone": "UTC",
    }


class DeliveryPolicyTests(unittest.TestCase):
    def test_floor_is_five_posts_with_four_hour_gap(self):
        self.assertEqual(delivery_policy.MINIMUM_POSTS_24H, 5)
        self.assertEqual(delivery_policy.MINIMUM_GAP_HOURS, 4)
        self.assertTrue(delivery_policy.LINE_QUOTA_EXHAUSTED)

    def test_slot_eligible_skips_line_placeholders_and_disabled(self):
        self.assertTrue(delivery_policy.slot_eligible(_primary("FB-CD", "facebook")))
        self.assertTrue(delivery_policy.slot_eligible(_primary("IG-CD", "instagram")))
        self.assertTrue(delivery_policy.slot_eligible(_primary("YT-CD", "youtube")))
        self.assertFalse(delivery_policy.slot_eligible(_primary(
            "IG-SWEDEN", "instagram", platform_account_id="",
        )))
        self.assertFalse(delivery_policy.slot_eligible({
            "account_id": "LINE-CD",
            "platform": "line",
            "enabled": True,
            "platform_account_id": "",
        }))
        self.assertFalse(delivery_policy.slot_eligible(_primary(
            "YT-JIYA", "youtube", enabled=False,
        )))

    def test_parse_queue_time_accepts_gviz_and_iso(self):
        iso = delivery_policy.parse_queue_time("2026-08-30T08:00:00Z")
        self.assertEqual(iso, datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc))
        gviz = delivery_policy.parse_queue_time("Date(2026,7,30,8,0,0)")
        self.assertEqual(gviz, datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc))

    def test_due_accounts_respect_four_hour_spacing(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        accounts = {
            "IG-SPAIN": _primary("IG-SPAIN", "instagram"),
            "IG-ITALY": _primary("IG-ITALY", "instagram"),
            "FB-CD": _primary("FB-CD", "facebook"),
            "IG-BLANK": _primary("IG-BLANK", "instagram", platform_account_id=""),
            "LINE-CD": {
                "account_id": "LINE-CD",
                "platform": "line",
                "enabled": True,
                "platform_account_id": "",
            },
        }
        activity = {
            "IG-SPAIN": {"count": 2, "last": now - timedelta(hours=6)},
            "IG-ITALY": {"count": 2, "last": now - timedelta(hours=2)},
            "FB-CD": {"count": 5, "last": now - timedelta(hours=6)},
        }
        due = delivery_policy.due_deficit_accounts(accounts, activity, now)
        self.assertEqual([item["account_id"] for item in due], ["IG-SPAIN"])
        self.assertEqual(due[0]["deficit"], 3)

        just_inside = {"IG-SPAIN": {"count": 1, "last": now - timedelta(hours=4) + timedelta(seconds=1)}}
        self.assertEqual(delivery_policy.due_deficit_accounts(
            {"IG-SPAIN": accounts["IG-SPAIN"]}, just_inside, now,
        ), [])
        exactly_four = {"IG-SPAIN": {"count": 1, "last": now - timedelta(hours=4)}}
        self.assertEqual(
            [item["account_id"] for item in delivery_policy.due_deficit_accounts(
                {"IG-SPAIN": accounts["IG-SPAIN"]}, exactly_four, now,
            )],
            ["IG-SPAIN"],
        )

    def test_rolling_activity_counts_only_successes_in_24h(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        accounts = {"FB-CD": _primary("FB-CD", "facebook")}
        records = [
            {"account_id": "FB-CD", "status": "uploaded", "last_attempt_at": "2026-08-30 08:00:00"},
            {"account_id": "FB-CD", "status": "uploaded", "last_attempt_at": "2026-08-29 13:00:00"},
            {"account_id": "FB-CD", "status": "failed", "last_attempt_at": "2026-08-30 10:00:00"},
            {"account_id": "FB-CD", "status": "uploaded", "last_attempt_at": "2026-08-29 11:59:59"},
            {"account_id": "LINE-CD", "status": "uploaded", "last_attempt_at": "2026-08-30 08:00:00"},
        ]
        activity = delivery_policy.rolling_activity(records, accounts, now)
        self.assertEqual(activity["FB-CD"]["count"], 2)
        self.assertNotIn("LINE-CD", activity)

    def test_platform_limits_cover_all_ready_accounts_and_skip_line(self):
        accounts = {}
        for idx in range(21):
            aid = "FB-%02d" % idx
            accounts[aid] = _primary(aid, "facebook")
        for idx in range(17):
            aid = "IG-%02d" % idx
            accounts[aid] = _primary(aid, "instagram")
        accounts["IG-SWEDEN"] = _primary("IG-SWEDEN", "instagram", platform_account_id="")
        accounts["YT-CD"] = _primary("YT-CD", "youtube")
        accounts["LINE-CD"] = {
            "account_id": "LINE-CD",
            "platform": "line",
            "enabled": True,
            "platform_account_id": "",
        }
        slots = optimized_runner._platform_limits(50, accounts)
        self.assertEqual(slots, {
            "facebook": 21,
            "instagram": 17,
            "youtube": 1,
            "line": 0,
        })
        self.assertEqual(sum(slots.values()), 39)

    def test_platform_limits_fallback_excludes_line_at_production_cap(self):
        slots = optimized_runner._platform_limits(50)
        self.assertEqual(slots["line"], 0)
        self.assertEqual(sum(slots.values()), 50)
        self.assertGreaterEqual(slots["facebook"], 1)
        self.assertGreaterEqual(slots["instagram"], 1)
        self.assertGreaterEqual(slots["youtube"], 1)

    def test_healthy_candidates_select_every_ready_primary_account(self):
        jobs = []
        accounts = {}
        sources = {"100": {"sku": "100"}}
        ready_ids = ["FB-A", "FB-B", "IG-A", "IG-B", "YT-CD"]
        for account_id in ready_ids:
            platform = "facebook" if account_id.startswith("FB") else (
                "youtube" if account_id.startswith("YT") else "instagram"
            )
            accounts[account_id] = _primary(account_id, platform)
            jobs.append({
                "job_id": "100-%s-carousel" % account_id,
                "account_id": account_id,
                "platform": platform,
                "sku": "100",
                "row": 3,
                "attempts": 0,
                "notes": "",
            })
        accounts["LINE-CD"] = {
            "account_id": "LINE-CD",
            "platform": "line",
            "enabled": True,
            "platform_account_id": "",
        }
        jobs.append({
            "job_id": "100-LINE-CD-carousel",
            "account_id": "LINE-CD",
            "platform": "line",
            "sku": "100",
            "row": 9,
            "attempts": 0,
            "notes": "",
        })
        sheets = type("Sheets", (), {"update_job": staticmethod(lambda *a, **k: None)})()
        activity = {
            aid: {"count": 0, "last": None, "success_times": []}
            for aid in ready_ids
        }
        with patch("optimized_runner.resolve_media_fixed", return_value=["https://example.com/a.jpg"]), \
             patch("optimized_runner._dns_resolves", return_value=True), \
             patch("optimized_runner.main._classify_media_url", return_value="image"), \
             patch("optimized_runner._local_slot_due", return_value=False), \
             patch("optimized_runner._video_validation_reason", return_value=""):
            selected = optimized_runner._healthy_candidates(
                jobs, accounts, sources, sheets, limit=50,
                recent_upload_activity=activity,
            )
        self.assertEqual(
            sorted(job["account_id"] for job in selected),
            sorted(ready_ids),
        )
        self.assertNotIn("LINE-CD", [job["account_id"] for job in selected])


if __name__ == "__main__":
    unittest.main()
