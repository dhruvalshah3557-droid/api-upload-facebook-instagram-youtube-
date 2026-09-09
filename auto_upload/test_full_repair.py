#!/usr/bin/env python3
import os
import sys
import types
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

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

import optimized_runner


class FullRepairTests(unittest.TestCase):
    def test_instagram_carousel_does_not_embed_product_video(self):
        source = {
            "main_image": "https://media.example/center.jpg",
            "certificate_media_url": "https://media.example/certificate.jpg",
            "side_images": ["https://media.example/side.jpg"],
            "video_url": "https://media.example/product.mp4",
        }
        job = {
            "platform": "instagram",
            "media_selection": "carousel",
        }
        media = optimized_runner.resolve_media_fixed(job, source)
        self.assertEqual(media, [
            "https://media.example/center.jpg",
            "https://media.example/certificate.jpg",
            "https://media.example/side.jpg",
        ])
        self.assertNotIn(source["video_url"], media)

    def test_regional_instagram_does_not_match_generic_colour_diam_page(self):
        uploader = optimized_runner.main.InstagramUploader
        uploader._IG_ID_CACHE.clear()

        def response(data):
            return types.SimpleNamespace(
                json=lambda: {"data": data},
                raise_for_status=lambda: None,
            )

        generic = [{
            "id": "PAGE-CD",
            "name": "Colour Diam",
            "instagram_business_account": {
                "id": "IG-CD",
                "username": "colourdiamonds",
            },
        }]
        with patch("instagram_uploader.requests.get", return_value=response(generic)):
            with self.assertRaises(optimized_runner.main.IGAccountNotLinkedError):
                uploader._resolve_ig_user_id("", "token", "Colour Diam Sweden")

        linked = [{
            "id": "PAGE-SWEDEN",
            "name": "Regional Page",
            "instagram_business_account": {
                "id": "IG-SWEDEN-ID",
                "username": "colourdiamsweden",
            },
        }]
        with patch("instagram_uploader.requests.get", return_value=response(linked)):
            self.assertEqual(
                uploader._resolve_ig_user_id("", "token", "Colour Diam Sweden"),
                "IG-SWEDEN-ID",
            )

    def test_blank_active_instagram_does_not_consume_valid_account_slot(self):
        jobs = [
            {
                "job_id": "1-IG-BLANK-carousel",
                "account_id": "IG-BLANK",
                "platform": "instagram",
                "sku": "1",
                "row": 2,
                "attempts": 0,
                "notes": "",
            },
            {
                "job_id": "1-IG-VALID-carousel",
                "account_id": "IG-VALID",
                "platform": "instagram",
                "sku": "1",
                "row": 3,
                "attempts": 0,
                "notes": "",
            },
        ]
        accounts = {
            "IG-BLANK": {
                "enabled": True,
                "platform": "instagram",
                "platform_account_id": "",
                "account_name": "Colour Diam Sweden",
                "credential_property_key": "META_TOKEN_IG_SWEDEN",
                "timezone": "Europe/Stockholm",
            },
            "IG-VALID": {
                "enabled": True,
                "platform": "instagram",
                "platform_account_id": "17841400000000000",
                "account_name": "Colour Diam Valid",
                "credential_property_key": "META_TOKEN_IG_VALID",
                "timezone": "UTC",
            },
        }
        sources = {"1": {"sku": "1"}}
        sheets = types.SimpleNamespace(update_job=lambda *args, **kwargs: None)

        with patch("optimized_runner.Config.get_token", return_value=""), \
             patch("optimized_runner.resolve_media_fixed", return_value=["https://media.example/center.jpg"]), \
             patch("optimized_runner._dns_resolves", return_value=True), \
             patch("optimized_runner.main._classify_media_url", return_value="image"):
            selected = optimized_runner._healthy_candidates(
                jobs,
                accounts,
                sources,
                sheets,
                limit=20,
                recent_upload_activity={},
            )

        self.assertEqual(
            [job["account_id"] for job in selected],
            ["IG-VALID"],
        )


    def test_deep_backlog_scan_keeps_fresh_jobs_and_reaches_oldest(self):
        jobs = [{"job_id": str(i)} for i in range(1000)]
        sampled = optimized_runner._account_scan_jobs(jobs, limit=300)
        self.assertEqual(len(sampled), 300)
        self.assertEqual([j["job_id"] for j in sampled[:100]], [str(i) for i in range(100)])
        self.assertEqual(sampled[-1]["job_id"], "999")
        self.assertGreater(len({j["job_id"] for j in sampled}), 290)

    def test_recent_instagram_rate_limit_pauses_instagram_only(self):
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        jobs = [{
            "platform": "instagram",
            "error_message": "META_RATE_LIMIT code=4 subcode=2207051",
            "last_attempt_at": "2026-09-09T11:55:00",
        }]
        active, wait = optimized_runner._instagram_rate_limit_active(jobs, now)
        self.assertTrue(active)
        self.assertGreater(wait, 0)


if __name__ == "__main__":
    unittest.main()
