#!/usr/bin/env python3
"""Tests for the YouTube token health monitor."""
import os
import unittest
from unittest import mock

import youtube_token_health as health


class ClientPairTest(unittest.TestCase):
    def test_colour_diam_uses_dedicated_client(self):
        self.assertEqual(
            health.client_pair("YOUTUBE_OAUTH_REFRESH_TOKEN_COLOURDIAMONDSS"),
            ("YOUTUBE_CLIENT_ID_COLOURDIAMONDSS",
             "YOUTUBE_CLIENT_SECRET_COLOURDIAMONDSS"),
        )

    def test_other_channels_use_shared_client(self):
        for key in (
            "YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA",
            "YOUTUBE_OAUTH_REFRESH_TOKEN_TRENDINGJEWELS",
            "YOUTUBE_OAUTH_REFRESH_TOKEN_TREND",
        ):
            self.assertEqual(
                health.client_pair(key),
                ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
            )


class CheckTokenTest(unittest.TestCase):
    def test_missing_refresh_token_is_dead(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            ok, detail = health.check_token("YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA")
        self.assertFalse(ok)
        self.assertIn("not set", detail)

    def test_missing_client_credentials_is_dead(self):
        with mock.patch.dict(
            os.environ,
            {"YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA": "token"},
            clear=True,
        ):
            ok, detail = health.check_token("YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA")
        self.assertFalse(ok)
        self.assertIn("client id/secret missing", detail)

    def test_successful_exchange_is_healthy(self):
        env = {
            "YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA": "token",
            "YOUTUBE_CLIENT_ID": "id",
            "YOUTUBE_CLIENT_SECRET": "secret",
        }
        response = mock.MagicMock()
        response.read.return_value = b'{"access_token": "abc"}'
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("urllib.request.urlopen", return_value=response):
                ok, detail = health.check_token("YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA")
        self.assertTrue(ok)
        self.assertEqual(detail, "ok")


if __name__ == "__main__":
    unittest.main()
