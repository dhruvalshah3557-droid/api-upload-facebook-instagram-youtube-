#!/usr/bin/env python3
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _install(name, attrs=None):
    mod = types.ModuleType(name)
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    parent = sys.modules.get(".".join(name.split(".")[:-1]))
    if parent is not None:
        setattr(parent, name.split(".")[-1], mod)
    return mod


def _ensure(name, attrs=None):
    if name in sys.modules:
        mod = sys.modules[name]
        for key, value in (attrs or {}).items():
            if not hasattr(mod, key):
                setattr(mod, key, value)
        return mod
    return _install(name, attrs)


_ensure("gspread", {
    "service_account": lambda *a, **k: None,
    "authorize": lambda *a, **k: None,
    "WorksheetNotFound": type("WorksheetNotFound", (Exception,), {}),
})
sys.modules["gspread"].exceptions = getattr(
    sys.modules["gspread"],
    "exceptions",
    types.SimpleNamespace(APIError=Exception, WorksheetNotFound=sys.modules["gspread"].WorksheetNotFound),
)
_ensure("dotenv", {"load_dotenv": lambda *a, **k: None})
_ensure("requests", {
    "get": lambda *a, **k: None,
    "post": lambda *a, **k: None,
    "RequestException": Exception,
    "Timeout": type("Timeout", (Exception,), {}),
    "ConnectionError": type("ConnectionError", (Exception,), {}),
})
_ensure("google_auth_oauthlib")
_ensure("google_auth_oauthlib.flow", {"InstalledAppFlow": lambda *a, **k: None})
_ensure("google.auth")
_ensure("google.auth.transport")
_ensure("google.auth.transport.requests", {"Request": lambda *a, **k: None})
_ensure("google.oauth2")
_ensure("google.oauth2.credentials", {"Credentials": lambda *a, **k: None})
_ensure(
    "google.oauth2.service_account",
    {"Credentials": types.SimpleNamespace(from_service_account_file=lambda *a, **k: None)},
)
_ensure("googleapiclient")
_ensure("googleapiclient.discovery", {"build": lambda *a, **k: None})
_ensure("googleapiclient.http", {"MediaIoBaseUpload": lambda *a, **k: None})
_ensure("deep_translator", {"GoogleTranslator": lambda *a, **k: None})
_ensure("edge_tts", {"list_voices": lambda: [], "Communicate": lambda *a, **k: None})

import voice_preview  # noqa: E402


class VoicePreviewAutomaticTests(unittest.TestCase):
    def test_incomplete_preview_error_detects_missing_media(self):
        self.assertTrue(voice_preview._incomplete_preview_error(
            ValueError("SKU 4281 needs video in S/U and PRODUCT DESCRIPTION in AE")
        ))
        self.assertTrue(voice_preview._incomplete_preview_error(
            ValueError("SKU 1 not found in jewellery stock or diamond stock")
        ))
        self.assertFalse(voice_preview._incomplete_preview_error(
            RuntimeError("Sheets connection failed")
        ))

    @patch("voice_preview.generate")
    @patch("voice_preview._worksheet")
    @patch("voice_preview._client")
    def test_generate_automatic_skips_incomplete_skus_without_failing(
        self, client_fn, worksheet_fn, generate_fn
    ):
        worksheet_fn.return_value = types.SimpleNamespace(get_all_records=lambda: [])
        queue_ws = types.SimpleNamespace(get_all_records=lambda: [
            {"status": "pending", "media_selection": "product_video", "sku": "4281", "language": "th"},
            {"status": "pending", "media_selection": "product_video", "sku": "4281", "language": "en"},
            {"status": "pending", "media_selection": "product_video", "sku": "5000", "language": "en"},
        ])
        book = types.SimpleNamespace(worksheet=lambda name: queue_ws)
        client_fn.return_value = types.SimpleNamespace(open_by_url=lambda url: book)

        def fake_generate(sku, language, client=None, auto_approve=True):
            if sku == "4281":
                raise ValueError(f"SKU {sku} needs video in S/U and PRODUCT DESCRIPTION in AE")

        generate_fn.side_effect = fake_generate
        voice_preview.generate_automatic(limit=2)
        skus = [call.args[0] for call in generate_fn.call_args_list]
        self.assertEqual(skus, ["4281", "5000"])

    @patch("voice_preview.generate")
    @patch("voice_preview._worksheet")
    @patch("voice_preview._client")
    def test_generate_automatic_still_fails_on_real_errors(
        self, client_fn, worksheet_fn, generate_fn
    ):
        worksheet_fn.return_value = types.SimpleNamespace(get_all_records=lambda: [])
        queue_ws = types.SimpleNamespace(get_all_records=lambda: [
            {"status": "pending", "media_selection": "product_video", "sku": "5000", "language": "en"},
        ])
        book = types.SimpleNamespace(worksheet=lambda name: queue_ws)
        client_fn.return_value = types.SimpleNamespace(open_by_url=lambda url: book)
        generate_fn.side_effect = RuntimeError("Sheets connection failed")
        with self.assertRaises(RuntimeError):
            voice_preview.generate_automatic(limit=1)


if __name__ == "__main__":
    unittest.main()
