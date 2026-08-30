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


if __name__ == "__main__":
    unittest.main()
