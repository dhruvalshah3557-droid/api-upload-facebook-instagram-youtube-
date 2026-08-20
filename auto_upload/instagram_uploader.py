import logging
import time

import requests

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def _is_video_url(url):
    return any(ext in url.lower() for ext in _VIDEO_EXTS)


class InstagramUploader:
    def __init__(self, ig_user_id, access_token, page_name=""):
        self.ig_user_id = ig_user_id
        self.access_token = access_token
        self.page_name = page_name

    def _create_media_container(self, media_url, caption, is_video=False, product_id="", carousel_item=False):
        params = {"access_token": self.access_token}
        if carousel_item:
            params["is_carousel_item"] = "true"
        else:
            params["caption"] = caption
        if is_video:
            # Standalone posts use REELS; carousel children must use VIDEO.
            params["media_type"] = "REELS" if not carousel_item else "VIDEO"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
        if product_id and not carousel_item:
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

    def _create_carousel_container(self, child_ids, caption, product_id=""):
        params = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": self.access_token,
        }
        if product_id:
            params["product_tags"] = f"[{{\"product_id\":\"{product_id}\"}}]"
        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media"
        logger.info(f"[{self.page_name}] Creating IG carousel container")
        resp = requests.post(url, data=params, timeout=60)
        result = resp.json()
        if "id" not in result:
            logger.error(f"[{self.page_name}] Carousel container failed: {result}")
            raise Exception(result.get("error", {}).get("message", str(result)))
        logger.info(f"[{self.page_name}] Carousel container created: {result['id']}")
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

    def upload_carousel(self, media_urls, caption, product_id=""):
        child_ids = []
        for media_url in media_urls:
            child_id = self._create_media_container(
                media_url, caption, is_video=_is_video_url(media_url), product_id="", carousel_item=True
            )
            child_ids.append(child_id)
            time.sleep(5)
        container_id = self._create_carousel_container(child_ids, caption, product_id)
        time.sleep(15)
        return self._publish_container(container_id)

    def upload(self, media_url, caption, product_id=""):
        is_video = _is_video_url(media_url)
        container_id = self._create_media_container(media_url, caption, is_video, product_id)
        wait = 30 if is_video else 5
        logger.info(f"[{self.page_name}] Waiting {wait}s for media processing...")
        time.sleep(wait)
        return self._publish_container(container_id)
