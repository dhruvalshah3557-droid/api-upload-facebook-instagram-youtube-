import logging
import os

import requests

from media_prep import prepare_video

logger = logging.getLogger(__name__)

TIKTOK_BASE = "https://open.tiktokapis.com/v2"

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def _env(key, default=""):
    value = os.getenv(key)
    return value.strip() if value is not None and value.strip() else default


def _is_video_url(url):
    return any(ext in url.lower() for ext in _VIDEO_EXTS)


class TikTokUploader:
    """Publish videos via the TikTok Content Posting API v2.

    Flow: POST /post/publish/video/init/ -> PUT the video bytes to the returned
    upload_url (single chunk) -> POST /post/publish/video/finalize/. The access
    token must carry the `video.publish` scope and be for a Content Posting API
    app (not the old share/creator endpoints).
    """

    def __init__(self, access_token="", account_name=""):
        self.access_token = access_token or _env("TIKTOK_ACCESS_TOKEN")
        self.account_name = account_name
        if not self.access_token:
            raise Exception(
                "TikTok access token missing: set TIKTOK_ACCESS_TOKEN"
            )

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _init_upload(self, video_size, title):
        body = {
            "post_info": {
                "title": (title or "")[:150],
                "privacy_level": "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
            "draft": False,
        }
        resp = requests.post(
            f"{TIKTOK_BASE}/post/publish/video/init/",
            headers=self._headers(),
            json=body,
            timeout=60,
        )
        data = resp.json()
        result = data.get("data", {})
        publish_id = result.get("publish_id")
        if not publish_id:
            raise Exception(f"TikTok init error: {data}")
        upload_url = result.get("upload_url", "")
        logger.info(f"[{self.account_name}] TikTok publish_id={publish_id}")
        return publish_id, upload_url

    def _upload_content(self, upload_url, content):
        resp = requests.put(
            upload_url,
            data=content,
            headers={"Content-Type": "video/mp4"},
            timeout=600,
        )
        if resp.status_code not in (200, 201, 204):
            raise Exception(f"TikTok upload error {resp.status_code}: {resp.text[:500]}")

    def _finalize(self, publish_id):
        resp = requests.post(
            f"{TIKTOK_BASE}/post/publish/video/finalize/",
            headers=self._headers(),
            json={"publish_id": publish_id},
            timeout=60,
        )
        data = resp.json()
        if not data.get("data", {}).get("publish_id"):
            raise Exception(f"TikTok finalize error: {data}")
        logger.info(f"[{self.account_name}] TikTok video published: {publish_id}")

    def upload(self, media_url, title="", description="", is_video=None):
        """Publish a single video (TikTok is video-only)."""
        name, content, content_type = prepare_video(media_url)
        publish_id, upload_url = self._init_upload(len(content), title)
        self._upload_content(upload_url, content)
        self._finalize(publish_id)
        return {"id": publish_id, "url": f"https://www.tiktok.com/@{self.account_name}/video/{publish_id}"}

    def upload_carousel(self, image_urls, caption="", product_id=""):
        """TikTok does not support carousel/image posts via the Content Posting API."""
        raise Exception("TikTok supports video posts only; carousel jobs should not be generated")
