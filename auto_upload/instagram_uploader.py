import logging
import os
import shutil
import subprocess
import time

import requests

from media_prep import prepare_video

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v19.0"

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def _is_video_url(url):
    return any(ext in url.lower() for ext in _VIDEO_EXTS)


class IGAccountNotLinkedError(Exception):
    """The configured Facebook page is not linked to an Instagram Business
    Account, so the IG Graph API cannot publish to it.
    """


def _probe_video(media_url):
    """Return (width, height) of a video, or None if it cannot be probed."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
             media_url],
            check=False, capture_output=True, text=True, timeout=60,
        )
        line = out.stdout.strip()
        if not line:
            return None
        w, h = line.split("x")
        return int(w), int(h)
    except Exception:
        return None


class InstagramUploader:
    _IG_ID_CACHE = {}

    def __init__(self, ig_user_id, access_token, page_name=""):
        self.ig_user_id = self._resolve_ig_user_id(ig_user_id, access_token, page_name)
        self.access_token = access_token
        self.page_name = page_name

    @classmethod
    def _resolve_ig_user_id(cls, configured_id, access_token, page_name=""):
        configured_id = str(configured_id or "").strip()
        cache_key = configured_id or page_name
        if cache_key and cache_key in cls._IG_ID_CACHE:
            return cls._IG_ID_CACHE[cache_key]

        if configured_id:
            cls._IG_ID_CACHE[cache_key] = configured_id
            return configured_id

        try:
            payload = requests.get(
                f"{FB_GRAPH_URL}/me/accounts",
                params={
                    "fields": "id,name,instagram_business_account{id,username}",
                    "access_token": access_token,
                },
                timeout=15,
            ).json()
            pages = payload.get("data", [])
        except Exception:
            pages = []

        matches = []
        wanted = str(page_name or "").strip().lower()
        for page in pages:
            ig = page.get("instagram_business_account") or {}
            ig_id = str(ig.get("id", "") or "").strip()
            if not ig_id:
                continue
            page_label = str(page.get("name", "") or "").strip().lower()
            ig_username = str(ig.get("username", "") or "").strip().lower()
            if not wanted or wanted in page_label or wanted in ig_username:
                matches.append(ig_id)

        if len(matches) == 1:
            resolved = matches[0]
        elif len(matches) > 1 and not wanted:
            resolved = matches[0]
        else:
            resolved = ""

        if resolved and cache_key:
            cls._IG_ID_CACHE[cache_key] = resolved
        if not resolved:
            raise IGAccountNotLinkedError(
                f"Could not resolve Instagram Business Account ID for '{page_name}'. "
                "Set the verified Instagram Business ID in Accounts.platform_account_id."
            )
        return resolved

    def _create_media_container(self, media_url, caption, is_video=False, product_id="", carousel_item=False):
        params = {"access_token": self.access_token}
        if carousel_item:
            params["is_carousel_item"] = "true"
        else:
            params["caption"] = caption
        if is_video:
            # Carousel video children must explicitly identify themselves as VIDEO.
            # Standalone Instagram videos are published as REELS.
            params["media_type"] = "VIDEO" if carousel_item else "REELS"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
        if product_id and not carousel_item:
            params["product_tags"] = f"[{{\"product_id\":\"{product_id}\"}}]"

        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media"
        files = None
        if is_video and not carousel_item:
            params.pop("video_url", None)
            files = {"video": prepare_video(media_url)}
        logger.info(f"[{self.page_name}] Creating IG {'reel' if is_video and not carousel_item else 'video' if is_video else 'image'} container")
        resp = requests.post(url, data=params, files=files, timeout=60)
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

    def _publish_container(self, container_id, retries=4):
        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media_publish"
        params = {"creation_id": container_id, "access_token": self.access_token}
        last_error = None
        for attempt in range(retries + 1):
            logger.info(
                f"[{self.page_name}] Publishing container {container_id} "
                f"(attempt {attempt + 1}/{retries + 1})"
            )
            resp = requests.post(url, data=params, timeout=60)
            result = resp.json()
            if "id" in result:
                logger.info(f"[{self.page_name}] IG post published: {result['id']}")
                return result
            message = result.get("error", {}).get("message", str(result))
            code = result.get("error", {}).get("code")
            transient = code in (9007,) or "not available" in message.lower() or "processing" in message.lower()
            if not transient:
                logger.error(f"[{self.page_name}] Publish failed: {result}")
                raise Exception(message)
            last_error = Exception(message)
            wait = 30 * (attempt + 1)
            logger.warning(
                f"[{self.page_name}] Media not ready yet ({message}); "
                f"retrying in {wait}s"
            )
            time.sleep(wait)
        raise last_error

    def upload_carousel(self, media_urls, caption, product_id=""):
        # Preserve source order. If broken media filtering leaves only one item,
        # publish it normally rather than creating an invalid one-child carousel.
        media_urls = [str(url or "").strip() for url in media_urls if str(url or "").strip()]
        if not media_urls:
            raise Exception("Instagram carousel has no usable media")
        if len(media_urls) == 1:
            logger.warning(f"[{self.page_name}] Carousel reduced to one media item; publishing as a single post")
            return self.upload(media_urls[0], caption, product_id)

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

    def permalink(self, media_id):
        if not media_id:
            return ""
        try:
            resp = requests.get(
                f"{FB_GRAPH_URL}/{media_id}",
                params={"fields": "permalink", "access_token": self.access_token},
                timeout=15,
            )
            data = resp.json()
            return str(data.get("permalink", "") or "").strip()
        except Exception:
            return ""
