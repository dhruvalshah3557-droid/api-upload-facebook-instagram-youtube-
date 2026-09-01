#!/usr/bin/env python3
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


if __name__ == "__main__":
    unittest.main()
