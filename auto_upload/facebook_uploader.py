import os
import json
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"


class FacebookUploader:
    def __init__(self, page_id, page_token, page_name=""):
        self.page_id = page_id
        self.access_token = page_token
        self.page_name = page_name

    def upload_photo(self, media_url, caption, product_id=""):
        url = f"{FB_GRAPH_URL}/{self.page_id}/photos"
        params = {
            "url": media_url,
            "caption": caption,
            "access_token": self.access_token,
        }
        if product_id:
            params["product_tags"] = json.dumps([{"product_id": product_id}])
        logger.info(f"[{self.page_name}] Posting photo" + (" with product tag" if product_id else ""))
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] Photo posted: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Photo failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload_video(self, media_url, caption, product_id=""):
        url = f"{FB_GRAPH_URL}/{self.page_id}/videos"
        params = {
            "file_url": media_url,
            "description": caption,
            "access_token": self.access_token,
        }
        if product_id:
            params["product_tags"] = json.dumps([{"product_id": product_id}])
        logger.info(f"[{self.page_name}] Posting video" + (" with product tag" if product_id else ""))
        resp = requests.post(url, data=params, timeout=300)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] Video posted: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Video failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload(self, media_url, caption, product_id=""):
        is_video = any(ext in media_url.lower() for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"])
        if is_video:
            return self.upload_video(media_url, caption, product_id)
        return self.upload_photo(media_url, caption, product_id)
