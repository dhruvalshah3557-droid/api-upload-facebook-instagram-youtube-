import os
import time
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"


class InstagramUploader:
    def __init__(self, ig_user_id, access_token, page_name=""):
        self.ig_user_id = ig_user_id
        self.access_token = access_token
        self.page_name = page_name

    def _create_media_container(self, media_url, caption, is_video=False, product_id=""):
        params = {"access_token": self.access_token, "caption": caption}
        if is_video:
            params["media_type"] = "REELS"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
        if product_id:
            params["product_tags"] = f"[{{\"product_id\":\"{product_id}\"}}]"

        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media"
        logger.info(f"[{self.page_name}] Creating IG {'video' if is_video else 'image'} container")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" not in result:
            logger.error(f"[{self.page_name}] Container failed: {result}")
            raise Exception(result.get("error", {}).get("message", str(result)))
        logger.info(f"[{self.page_name}] Container created: {result['id']}")
        return result["id"]

    def _publish_container(self, container_id):
        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media_publish"
        params = {"creation_id": container_id, "access_token": self.access_token}
        logger.info(f"[{self.page_name}] Publishing container {container_id}")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] IG post published: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Publish failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload(self, media_url, caption, product_id=""):
        is_video = any(ext in media_url.lower() for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"])
        container_id = self._create_media_container(media_url, caption, is_video, product_id)
        time.sleep(5)
        return self._publish_container(container_id)
