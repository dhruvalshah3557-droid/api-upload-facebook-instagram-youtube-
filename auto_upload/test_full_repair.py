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
    def test_model_photo_is_resolved_as_independent_upload(self):
        source = {
            "model_images": [
                "https://colourdiam.com/Product/Model%20Photo%20Video/298/1.jpeg",
                "https://colourdiam.com/Product/Model%20Photo%20Video/298/2.jpeg",
            ]
        }
        self.assertEqual(
            optimized_runner.resolve_media_fixed(
                {"platform": "facebook", "media_selection": "model_photo:1"},
                source,
            ),
            ["https://colourdiam.com/Product/Model%20Photo%20Video/298/2.jpeg"],
        )

    def test_model_media_is_preferred_over_product_carousel(self):
        jobs = [
            {"job_id": "1-FB-CD-carousel", "sku": "1", "account_id": "FB-CD", "platform": "facebook", "format": "carousel", "media_selection": "carousel", "row": 30, "attempts": 0, "notes": ""},
            {"job_id": "2-FB-CD-model_photo-0", "sku": "2", "account_id": "FB-CD", "platform": "facebook", "format": "carousel", "media_selection": "model_photo:0", "row": 20, "attempts": 0, "notes": ""},
        ]
        accounts = {"FB-CD": {"enabled": True, "platform": "facebook", "timezone": "Asia/Bangkok"}}
        sources = {
            "1": {"main_image": "https://media.example/product.jpg", "side_images": []},
            "2": {"model_images": ["https://media.example/model.jpg"]},
        }
        sheets = types.SimpleNamespace(update_job=lambda *args: None)
        with patch("optimized_runner._platform_limits", return_value={"facebook": 1, "instagram": 0, "youtube": 0, "line": 0}), \
             patch("optimized_runner._local_slot_due", return_value=True), \
             patch("optimized_runner._rotation_rank", return_value=0), \
             patch("optimized_runner._is_clean_source", return_value=(True, "")), \
             patch("optimized_runner._media_preflight_reason", return_value=""):
            selected = optimized_runner._healthy_candidates(
                jobs, accounts, sources, sheets, limit=1
            )
        self.assertEqual(selected[0]["media_selection"], "model_photo:0")

    def test_linked_facebook_and_instagram_market_prefer_same_sku(self):
        jobs = [
            {"job_id": "101-FB-MMR-carousel", "sku": "101", "account_id": "FB-MMR", "platform": "facebook", "format": "carousel", "media_selection": "carousel", "row": 10, "attempts": 0, "notes": ""},
            {"job_id": "202-IG-MMR-carousel", "sku": "202", "account_id": "IG-MMR", "platform": "instagram", "format": "carousel", "media_selection": "carousel", "row": 30, "attempts": 0, "notes": ""},
            {"job_id": "101-IG-MMR-carousel", "sku": "101", "account_id": "IG-MMR", "platform": "instagram", "format": "carousel", "media_selection": "carousel", "row": 20, "attempts": 0, "notes": ""},
        ]
        accounts = {
            "FB-MMR": {"enabled": True, "platform": "facebook", "timezone": "Asia/Yangon"},
            "IG-MMR": {"enabled": True, "platform": "instagram", "platform_account_id": "17841430974311329", "timezone": "Asia/Yangon"},
        }
        sources = {
            "101": {"main_image": "https://media.example/101.jpg", "side_images": []},
            "202": {"main_image": "https://media.example/202.jpg", "side_images": []},
        }
        sheets = types.SimpleNamespace(update_job=lambda *args: None)
        with patch("optimized_runner._platform_limits", return_value={"facebook": 1, "instagram": 1, "youtube": 0, "line": 0}), \
             patch("optimized_runner._local_slot_due", return_value=True), \
             patch("optimized_runner._rotation_rank", return_value=0), \
             patch("optimized_runner._is_clean_source", return_value=(True, "")), \
             patch("optimized_runner._media_preflight_reason", return_value=""):
            selected = optimized_runner._healthy_candidates(
                jobs, accounts, sources, sheets, limit=2
            )

        self.assertEqual(
            [(job["account_id"], job["sku"]) for job in selected],
            [("FB-MMR", "101"), ("IG-MMR", "101")],
        )

    def test_same_product_different_format_is_blocked_per_account(self):
        records = [{
            "job_id": "298-IG-KUWAIT-carousel",
            "sku": "298",
            "account_id": "IG-KUWAIT",
            "platform": "instagram",
            "status": "uploaded",
            "notes": "",
        }]
        queue_ws = types.SimpleNamespace(get_all_records=lambda head: records)
        queue_sheets = types.SimpleNamespace(queue_ws=queue_ws, queue_header_row=1)
        reserved, _ = optimized_runner._queue_state(queue_sheets)

        duplicate_video = {
            "job_id": "298-IG-KUWAIT-product_video",
            "sku": "298",
            "account_id": "IG-KUWAIT",
            "platform": "instagram",
            "format": "video",
            "media_selection": "product_video",
            "row": 9,
            "attempts": 0,
            "notes": "",
        }
        updates = []
        sheets = types.SimpleNamespace(
            update_job=lambda job, values: updates.append((job, values))
        )
        accounts = {
            "IG-KUWAIT": {
                "enabled": True,
                "platform": "instagram",
                "platform_account_id": "17841436113237015",
                "timezone": "Asia/Kuwait",
            }
        }
        with patch("optimized_runner._local_slot_due", return_value=True), \
             patch("optimized_runner._rotation_rank", return_value=0):
            selected = optimized_runner._healthy_candidates(
                [duplicate_video], accounts, {}, sheets, limit=1,
                reserved_fingerprints=reserved,
            )

        self.assertEqual(selected, [])

    def test_caption_preflight_skips_unpublishable_job_and_picks_next(self):
        jobs = [
            {"job_id": "1263-FB-TURKEY-carousel", "sku": "1263", "account_id": "FB-TURKEY",
             "platform": "facebook", "format": "carousel", "media_selection": "carousel",
             "row": 30, "attempts": 0, "notes": ""},
            {"job_id": "1913-FB-TURKEY-carousel", "sku": "1913", "account_id": "FB-TURKEY",
             "platform": "facebook", "format": "carousel", "media_selection": "carousel",
             "row": 10, "attempts": 0, "notes": ""},
        ]
        accounts = {
            "FB-TURKEY": {
                "enabled": True,
                "platform": "facebook",
                "timezone": "Europe/Istanbul",
                "primary_language": "vi-VN",
            }
        }
        sources = {
            "1263": {
                "sku": "1263",
                "lang_captions": {},
                "product_name": "Ring",
                "main_image": "https://media.example/1263.jpg",
                "side_images": [],
            },
            "1913": {
                "sku": "1913",
                "lang_captions": {"vi": "Mo ta tieng Viet"},
                "product_name": "Ring",
                "main_image": "https://media.example/1913.jpg",
                "side_images": [],
            },
        }
        updates = []
        sheets = types.SimpleNamespace(
            update_job=lambda job, values: updates.append((job, values))
        )
        with patch("optimized_runner._platform_limits", return_value={"facebook": 1, "instagram": 0, "youtube": 0, "line": 0}), \
             patch("optimized_runner._local_slot_due", return_value=True), \
             patch("optimized_runner._rotation_rank", return_value=0), \
             patch("optimized_runner._is_clean_source", return_value=(True, "")), \
             patch("optimized_runner._media_preflight_reason", return_value=""):
            selected = optimized_runner._healthy_candidates(
                jobs, accounts, sources, sheets, limit=1
            )
        self.assertEqual(selected[0]["sku"], "1913")
        self.assertEqual(updates[0][0]["job_id"], "1263-FB-TURKEY-carousel")
        self.assertEqual(updates[0][1]["status"], "needs_review")

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

    def test_extensionless_video_is_forced_through_ffprobe_preflight(self):
        url = "https://media.example/download?id=broken-video"
        with patch("optimized_runner._dns_resolves", return_value=True), \
             patch("optimized_runner.main._classify_media_url", return_value="unknown"), \
             patch("optimized_runner._video_validation_reason",
                   return_value="audio-only source") as validate:
            reason = optimized_runner._media_preflight_reason(
                [url], force_video=True
            )

        self.assertIn("audio-only source", reason)
        validate.assert_called_once_with(url, force=True)

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
