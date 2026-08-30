import unittest
import sys
import types
from pathlib import Path
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace()

import media_prep


class MusicRotationTests(unittest.TestCase):
    def test_bundled_library_contains_pinned_real_tracks(self):
        tracks = media_prep.BUNDLED_CC0_MUSIC_URLS
        self.assertGreaterEqual(len(tracks), 6)
        self.assertTrue(all("2ce8458293fe4eeb91414a19d6d7ecd1562a5949" in track for track in tracks))

    def test_different_videos_rotate_across_library(self):
        keys = [f"https://media.example/product-{i}.mp4" for i in range(30)]
        selected = {media_prep._stable_choice(media_prep.BUNDLED_CC0_MUSIC_URLS, key) for key in keys}
        self.assertGreaterEqual(len(selected), 5)

    def test_same_video_is_stable(self):
        key = "https://media.example/product-6373.mp4"
        first = media_prep._stable_choice(media_prep.BUNDLED_CC0_MUSIC_URLS, key)
        second = media_prep._stable_choice(media_prep.BUNDLED_CC0_MUSIC_URLS, key)
        self.assertEqual(first, second)

    def test_legacy_single_url_cannot_force_one_track(self):
        with mock.patch.dict(
            media_prep.os.environ,
            {"BACKGROUND_MUSIC_URL": "https://example.com/one-track.mp3"},
            clear=True,
        ):
            self.assertEqual(media_prep._configured_music_urls(), [])


if __name__ == "__main__":
    unittest.main()
