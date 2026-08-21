import json
import logging

import requests

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"

_PAGE_TOKEN_CACHE = {}


class FacebookUploader:
    def __init__(self, page_id, page_token, page_name=""):
        self.page_id = page_id
        self.access_token = self._resolve_page_token(page_id, page_token)
        self.page_name = page_name

    @staticmethod
    def _download_media(media_url):
        """Download the media file bytes so the original is uploaded.

        Passing a URL to the Graph API makes Facebook re-fetch and re-compress
        the media, which degrades image/video quality. Uploading the original
        bytes via multipart preserves quality.
        """
        resp = requests.get(
            media_url,
            timeout=180,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )
        resp.raise_for_status()
        name = media_url.split("?")[0].rsplit("/", 1)[-1] or "media"
        return name, resp.content, resp.headers.get("Content-Type", "application/octet-stream")

    @classmethod
    def _resolve_page_token(cls, page_id, token):
        """Resolve a page-scoped token from a user token.

        Publishing must be done as the page itself; a user token fails with
        (#200) "Unpublished posts must be posted to a page as the page itself"
        and (#100) "No permission to publish the video". Page tokens are
        resolved via the Graph API and cached per page.
        """
        cached = _PAGE_TOKEN_CACHE.get(page_id)
        if cached:
            return cached
        try:
            resp = requests.get(
                f"{FB_GRAPH_URL}/{page_id}",
                params={"fields": "access_token", "access_token": token},
                timeout=15,
            )
            data = resp.json()
            resolved = data.get("access_token")
            if resolved:
                _PAGE_TOKEN_CACHE[page_id] = resolved
                return resolved
        except Exception:
            pass
        return token

    def upload_photo(self, media_url, caption, product_id=""):
        url = f"{FB_GRAPH_URL}/{self.page_id}/photos"
        data = {
            "caption": caption,
            "access_token": self.access_token,
        }
        if product_id:
            data["product_tags"] = json.dumps([{"product_id": product_id}])
        logger.info(f"[{self.page_name}] Posting photo" + (" with product tag" if product_id else ""))
        resp = requests.post(url, data=data, files={"source": self._download_media(media_url)}, timeout=120)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] Photo posted: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Photo failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload_video(self, media_url, caption, product_id=""):
        url = f"{FB_GRAPH_URL}/{self.page_id}/videos"
        data = {
            "description": caption,
            "access_token": self.access_token,
        }
        if product_id:
            data["product_tags"] = json.dumps([{"product_id": product_id}])
        logger.info(f"[{self.page_name}] Posting video" + (" with product tag" if product_id else ""))
        resp = requests.post(url, data=data, files={"source": self._download_media(media_url)}, timeout=600)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] Video posted: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Video failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload_carousel(self, image_urls, caption, product_id=""):
        """Publish a multi-image carousel post on the page.

        Children are created as unpublished photo objects, then attached to a
        feed post so the whole set publishes together as one carousel.
        """
        if not image_urls:
            raise Exception("No images provided for carousel")

        photos_url = f"{FB_GRAPH_URL}/{self.page_id}/photos"
        child_ids = []
        for image_url in image_urls:
            params = {
                "published": "false",
                "access_token": self.access_token,
            }
            resp = requests.post(photos_url, data=params, files={"source": self._download_media(image_url)}, timeout=120)
            result = resp.json()
            if "id" not in result:
                raise Exception(result.get("error", {}).get("message", str(result)))
            child_ids.append({"media_fbid": result["id"]})

        feed_url = f"{FB_GRAPH_URL}/{self.page_id}/feed"
        params = {
            "access_token": self.access_token,
            "message": caption,
            "attached_media": json.dumps(child_ids),
        }
        if product_id:
            params["product_tags"] = json.dumps([{"product_id": product_id}])
        logger.info(f"[{self.page_name}] Publishing carousel with {len(child_ids)} image(s)")
        resp = requests.post(feed_url, data=params, timeout=60)
        result = resp.json()
        if "id" in result:
            logger.info(f"[{self.page_name}] Carousel posted: {result['id']}")
            return result
        logger.error(f"[{self.page_name}] Carousel failed: {result}")
        raise Exception(result.get("error", {}).get("message", str(result)))

    def upload(self, media_url, caption, product_id=""):
        is_video = any(ext in media_url.lower() for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"])
        if is_video:
            return self.upload_video(media_url, caption, product_id)
        return self.upload_photo(media_url, caption, product_id)
