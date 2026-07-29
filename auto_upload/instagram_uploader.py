import os
import logging
import requests
import time
from config import Config

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"


class InstagramUploader:
    def __init__(self):
        self.ig_user_id = Config.IG_USER_ID
        self.access_token = Config.FB_ACCESS_TOKEN

    def _create_media_container(self, media_url, caption, is_video=False):
        endpoint = "media" if not is_video else "media"
        params = {
            "access_token": self.access_token,
            "caption": caption,
        }
        if is_video:
            params["media_type"] = "VIDEO"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url

        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/{endpoint}"
        logger.info(f"Creating Instagram {'video' if is_video else 'image'} container")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" not in result:
            logger.error(f"Container creation failed: {result}")
            raise Exception(result.get("error", {}).get("message", str(result)))
        logger.info(f"Container created: {result['id']}")
        return result["id"]

    def _publish_container(self, container_id):
        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media_publish"
        params = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }
        logger.info(f"Publishing container {container_id}")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" in result:
            logger.info(f"Instagram post published: {result['id']}")
            return result
        logger.error(f"Publish failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload(self, media_url, caption):
        is_video = any(ext in media_url.lower() for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"])

        container_id = self._create_media_container(media_url, caption, is_video)
        time.sleep(5)
        result = self._publish_container(container_id)
        return result
