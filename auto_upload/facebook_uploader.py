import os
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"


class FacebookUploader:
    def __init__(self):
        self.page_id = Config.FB_PAGE_ID
        self.access_token = Config.FB_ACCESS_TOKEN

    def upload_photo(self, media_url, caption):
        url = f"{FB_GRAPH_URL}/{self.page_id}/photos"
        params = {
            "url": media_url,
            "caption": caption,
            "access_token": self.access_token,
        }
        logger.info(f"Posting photo to Facebook page {self.page_id}")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" in result:
            logger.info(f"Facebook photo posted: {result['id']}")
            return result
        else:
            logger.error(f"Facebook photo failed: {result}")
            raise Exception(result.get("error", {}).get("message", str(result)))

    def upload_video(self, media_url, caption):
        url = f"{FB_GRAPH_URL}/{self.page_id}/videos"
        params = {
            "file_url": media_url,
            "description": caption,
            "access_token": self.access_token,
        }
        logger.info(f"Posting video to Facebook page {self.page_id}")
        resp = requests.post(url, data=params, timeout=300)
        result = resp.json()
        if "id" in result:
            logger.info(f"Facebook video posted: {result['id']}")
            return result
        else:
            logger.error(f"Facebook video failed: {result}")
            raise Exception(result.get("error", {}).get("message", str(result)))

    def upload(self, media_url, caption):
        is_video = any(ext in media_url.lower() for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"])
        if is_video:
            return self.upload_video(media_url, caption)
        return self.upload_photo(media_url, caption)
