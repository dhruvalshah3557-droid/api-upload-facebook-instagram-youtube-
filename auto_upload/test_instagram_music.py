import json
import sys
import types
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *args, **kwargs: None)

from instagram_uploader import InstagramUploader


class _Response:
    status_code = 200

    def json(self):
        return {
            "data": [
                {"audio_id": f"track-{i}", "title": f"Track {i}"}
                for i in range(8)
            ]
        }


class InstagramMusicRotationTests(unittest.TestCase):
    def setUp(self):
        InstagramUploader._LAST_AUDIO_BY_ACCOUNT.clear()

    def test_catalog_results_rotate_instead_of_always_first(self):
        uploader = InstagramUploader.__new__(InstagramUploader)
        uploader.ig_user_id = "123"
        uploader.access_token = "token"
        uploader.page_name = "Colour Diam"
        keys = [f"https://media.example/reel-{i}.mp4" for i in range(30)]
        env = {
            "IG_AUDIO_SEARCH_QUERIES":
                "elegant instrumental,luxury piano,cinematic fashion,soft ambient"
        }
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch("instagram_uploader.requests.get", return_value=_Response(), create=True):
            configurations = [
                json.loads(uploader._trending_audio_configuration(key))
                for key in keys
            ]
        self.assertGreaterEqual(len({c["audio_id"] for c in configurations}), 6)

    def test_vietnam_uses_regional_searches(self):
        uploader = InstagramUploader.__new__(InstagramUploader)
        uploader.ig_user_id = "vietnam-account"
        uploader.access_token = "token"
        uploader.page_name = "Colour Diam Vietnam"
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("instagram_uploader.requests.get", return_value=_Response(), create=True) as get:
            uploader._trending_audio_configuration("job-1")
        self.assertIn(get.call_args.kwargs["params"]["search_query"], {
            "Vietnam trending", "Vietnamese pop", "Vietnam luxury instrumental",
        })

    def test_same_track_is_not_repeated_consecutively(self):
        uploader = InstagramUploader.__new__(InstagramUploader)
        uploader.ig_user_id = "123"
        uploader.access_token = "token"
        uploader.page_name = "Colour Diam Vietnam"
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("instagram_uploader.requests.get", return_value=_Response(), create=True), \
             mock.patch("instagram_uploader.hashlib.sha256") as digest:
            digest.return_value.digest.return_value = bytes(32)
            first = json.loads(uploader._trending_audio_configuration("job-1"))["audio_id"]
            second = json.loads(uploader._trending_audio_configuration("job-2"))["audio_id"]
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
