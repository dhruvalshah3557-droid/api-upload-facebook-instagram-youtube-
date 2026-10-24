import sys
import types
import unittest
from unittest import mock

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace()

from facebook_uploader import FacebookUploader


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = ""

    def json(self):
        return self._payload


class FacebookReelsTests(unittest.TestCase):
    def test_video_uses_native_reels_start_upload_finish_flow(self):
        uploader = FacebookUploader.__new__(FacebookUploader)
        uploader.page_id = "page-123"
        uploader.access_token = "page-token"
        uploader.page_name = "Colour Diam Test"

        responses = [
            _Response({"video_id": "reel-456", "upload_url": "https://rupload.test/reel"}),
            _Response({"success": True}),
            _Response({"success": True}),
        ]
        with mock.patch(
            "facebook_uploader.prepare_video",
            return_value=("source.mp4", b"video-with-audio", "video/mp4"),
        ) as prepare, mock.patch(
            "facebook_uploader.video_layout_from_bytes", return_value="vertical",
        ), mock.patch(
            "facebook_uploader.requests.post", side_effect=responses
        ) as post:
            result = uploader.upload_video(
                "https://media.test/source.mp4", "Caption", "product-1"
            )

        self.assertEqual(result["id"], "reel-456")
        self.assertEqual(prepare.call_args.kwargs.get("platform"), "facebook")
        self.assertTrue(prepare.call_args.kwargs.get("fill_9x16"))
        self.assertEqual(post.call_count, 3)

        start = post.call_args_list[0]
        self.assertTrue(start.args[0].endswith("/page-123/video_reels"))
        self.assertEqual(start.kwargs["data"]["upload_phase"], "start")

        upload = post.call_args_list[1]
        self.assertEqual(upload.args[0], "https://rupload.test/reel")
        self.assertEqual(upload.kwargs["headers"]["file_size"], str(len(b"video-with-audio")))
        self.assertEqual(upload.kwargs["data"], b"video-with-audio")

        finish = post.call_args_list[2]
        self.assertTrue(finish.args[0].endswith("/page-123/video_reels"))
        self.assertEqual(finish.kwargs["data"]["upload_phase"], "finish")
        self.assertEqual(finish.kwargs["data"]["video_state"], "PUBLISHED")
        self.assertEqual(finish.kwargs["data"]["video_id"], "reel-456")
        self.assertNotIn("product_tags", finish.kwargs["data"])

    def test_landscape_video_keeps_original_size_on_page_videos(self):
        uploader = FacebookUploader.__new__(FacebookUploader)
        uploader.page_id = "page-123"
        uploader.access_token = "page-token"
        uploader.page_name = "Colour Diam Test"

        with mock.patch(
            "facebook_uploader.prepare_video",
            return_value=("source.mp4", b"landscape-video", "video/mp4"),
        ), mock.patch(
            "facebook_uploader.video_layout_from_bytes", return_value="landscape",
        ), mock.patch(
            "facebook_uploader.requests.post",
            return_value=_Response({"id": "video-789"}),
        ) as post:
            result = uploader.upload_video(
                "https://media.test/wide.mp4", "Caption", "product-1"
            )

        self.assertEqual(result["id"], "video-789")
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args.args[0].endswith("/page-123/videos"))
        self.assertEqual(
            post.call_args.kwargs["files"]["source"][1], b"landscape-video"
        )

    def test_page_token_uses_me_accounts_when_direct_lookup_fails(self):
        import facebook_uploader

        facebook_uploader._PAGE_TOKEN_CACHE.clear()
        responses = [
            _Response({"error": {"message": "(#100) Tried accessing nonexisting field (access_token)"}}),
            _Response({
                "data": [
                    {"id": "other-page", "access_token": "wrong"},
                    {"id": "page-123", "access_token": "page-scoped-token"},
                ]
            }),
        ]
        with mock.patch("facebook_uploader.requests.get", side_effect=responses) as get:
            resolved = FacebookUploader._resolve_page_token("page-123", "user-token")
        self.assertEqual(resolved, "page-scoped-token")
        self.assertEqual(facebook_uploader._PAGE_TOKEN_CACHE["page-123"], "page-scoped-token")
        self.assertTrue(get.call_args_list[0].args[0].endswith("/page-123"))
        self.assertTrue(get.call_args_list[1].args[0].endswith("/me/accounts"))

    def test_unresolved_page_token_does_not_fall_back_to_user_token(self):
        import facebook_uploader

        facebook_uploader._PAGE_TOKEN_CACHE.clear()
        with mock.patch(
            "facebook_uploader.requests.get",
            return_value=_Response({"data": [{"id": "other-page", "access_token": "x"}]}),
        ):
            with self.assertRaisesRegex(Exception, "could not resolve a page-scoped access token"):
                FacebookUploader._resolve_page_token("page-123", "user-token")


if __name__ == "__main__":
    unittest.main()
