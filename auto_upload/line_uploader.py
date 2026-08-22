import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)

LINE_BOT_API = "https://api.line.me/v2/bot/message/broadcast"

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def _is_video_url(url):
    return any(ext in url.lower() for ext in _VIDEO_EXTS)


class LineUploader:
    """Publish content to LINE Official Account followers via the Messaging API.

    LINE has no native timeline/carousel post; content is delivered as a
    broadcast message to all followers. A "carousel" is sent as a batch of up
    to five image messages per broadcast request. Media URLs must be publicly
    reachable HTTPS URLs (LINE fetches them itself).
    """

    _MAX_MESSAGES = 5

    def __init__(self, channel_token, account_name=""):
        self.channel_token = channel_token
        self.account_name = account_name

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.channel_token}",
            "X-Line-Retry-Key": uuid.uuid4().hex,
            "Content-Type": "application/json",
        }

    def _broadcast(self, messages):
        if not messages:
            raise Exception("No messages to broadcast")
        resp = requests.post(
            LINE_BOT_API,
            headers=self._headers(),
            json={"messages": messages},
            timeout=60,
        )
        if resp.status_code != 200:
            body = resp.text[:2000]
            raise Exception(f"LINE API error {resp.status_code}: {body}")
        result = resp.json()
        sent = result.get("sentMessages", [])
        if not sent:
            raise Exception(f"LINE broadcast returned no sentMessages: {result}")
        logger.info(f"[{self.account_name}] LINE broadcast sent {len(sent)} message(s)")
        return result

    def _image_message(self, media_url):
        return {
            "type": "image",
            "originalContentUrl": media_url,
            "previewImageUrl": media_url,
        }

    def _video_message(self, media_url, thumbnail_url=""):
        return {
            "type": "video",
            "originalContentUrl": media_url,
            "previewImageUrl": thumbnail_url or media_url,
        }

    def upload(self, media_url, caption="", is_video=None, thumbnail_url=""):
        """Send a single image or video message to all followers."""
        if is_video is None:
            is_video = _is_video_url(media_url)
        message = (
            self._video_message(media_url, thumbnail_url)
            if is_video
            else self._image_message(media_url)
        )
        result = self._broadcast([message])
        message_id = result["sentMessages"][0].get("id", "")
        return {"id": message_id, "url": f"https://line.me/"}

    def upload_carousel(self, image_urls, caption="", product_id=""):
        """Send up to 5 images as image messages in one broadcast (batched)."""
        messages = [self._image_message(u) for u in image_urls]
        ids = []
        for start in range(0, len(messages), self._MAX_MESSAGES):
            result = self._broadcast(messages[start:start + self._MAX_MESSAGES])
            ids.extend(m.get("id", "") for m in result.get("sentMessages", []))
            time.sleep(2)
        return {"id": ids[0] if ids else "", "url": f"https://line.me/"}
