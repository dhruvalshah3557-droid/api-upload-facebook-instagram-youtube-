import logging
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

    Raised instead of silently using the Facebook page ID as the IG user ID
    (which produces confusing API failures). Jobs stay pending and publish
    automatically once the account is linked in Meta Business Suite.
    """


class InstagramUploader:
    _IG_ID_CACHE = {}

    def __init__(self, ig_user_id, access_token, page_name=""):
        self.ig_user_id = self._resolve_ig_user_id(ig_user_id, access_token, page_name)
        self.access_token = access_token
        self.page_name = page_name

    @classmethod
    def _resolve_ig_user_id(cls, configured_id, access_token, page_name=""):
        """Resolve the real Instagram Business Account ID from a page token.

        The Accounts sheet may hold the Facebook page ID (or be empty) for an
        Instagram account, but the IG Graph API needs the IG business account
        ID. Resolve it via the Graph API and cache it per account.
        """
        configured_id = str(configured_id or "").strip()
        cache_key = configured_id or page_name
        if cache_key and cache_key in cls._IG_ID_CACHE:
            return cls._IG_ID_CACHE[cache_key]

        def _graph_get(node_id, fields=""):
            try:
                resp = requests.get(
                    f"{FB_GRAPH_URL}/{node_id}",
                    params={"fields": fields, "access_token": access_token},
                    timeout=15,
                )
                return resp.json()
            except Exception:
                return {}

        resolved = ""
        if configured_id:
            data = _graph_get(configured_id, "instagram_business_account,id,media_count,ig_id")
            ig = data.get("instagram_business_account") or {}
            resolved = str(ig.get("id", "") or "").strip()
            if not resolved and ("media_count" in data or "ig_id" in data):
                resolved = configured_id
            if not resolved:
                api_err = (data.get("error") or {}).get("message", "")
                if data.get("id") or api_err:
                    detail = f" (API: {api_err})" if api_err else ""
                    raise IGAccountNotLinkedError(
                        f"No Instagram Business Account is linked to node "
                        f"{configured_id}{detail}. Link the Instagram account "
                        f"'{page_name}' to this Facebook page in Meta Business "
                        "Suite; the pipeline will resolve it automatically."
                    )
                raise Exception(
                    f"Could not resolve Instagram business account ID for "
                    f"{configured_id} (Graph API unavailable); will retry"
                )
        else:
            try:
                pages = requests.get(
                    f"{FB_GRAPH_URL}/me/accounts",
                    params={"access_token": access_token},
                    timeout=15,
                ).json().get("data", [])
            except Exception:
                pages = []
            matches = []
            for page in pages:
                ig = page.get("instagram_business_account") or {}
                ig_id = str(ig.get("id", "") or "").strip()
                if not ig_id:
                    continue
                name = str(page.get("name", "") or "")
                if not page_name or page_name.lower() in name.lower():
                    matches.append(ig_id)
            if len(matches) == 1:
                resolved = matches[0]
            elif len(matches) > 1 and not page_name:
                resolved = matches[0]
            elif not matches and len(pages) == 1:
                ig = pages[0].get("instagram_business_account") or {}
                resolved = str(ig.get("id", "") or "").strip()

        if resolved and cache_key:
            cls._IG_ID_CACHE[cache_key] = resolved
        if not resolved:
            raise Exception(
                "Could not resolve Instagram business account ID; link the "
                "page to an IG business account or set platform_account_id"
            )
        return resolved

    def _create_media_container(self, media_url, caption, is_video=False, product_id="", carousel_item=False):
        params = {"access_token": self.access_token}
        if carousel_item:
            params["is_carousel_item"] = "true"
        else:
            params["caption"] = caption
        if is_video:
            params["media_type"] = "REELS" if not carousel_item else "VIDEO"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
        if product_id and not carousel_item:
            params["product_tags"] = f"[{{\"product_id\":\"{product_id}\"}}]"

        url = f"{FB_GRAPH_URL}/{self.ig_user_id}/media"
        files = None
        if is_video and not carousel_item:
            params.pop("video_url", None)
            files = {"video": prepare_video(media_url, fill_9x16=True)}
        logger.info(f"[{self.page_name}] Creating IG {'video' if is_video else 'image'} container")
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
        # Keep the first video as the first carousel card. main.py supplies the
        # remaining order as main product image -> certificate -> other images.
        # Python's stable sort preserves the relative order of all non-video
        # cards while moving video card(s) to the front.
        media_urls = sorted(media_urls, key=lambda url: 0 if _is_video_url(url) else 1)
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
        """Resolve the canonical public URL for a published media object.

        The media_publish response returns the numeric media ID, but the
        https://www.instagram.com/p/<numeric-id> form only serves a login/blank
        page. The Graph API `permalink` field returns the real shortcode URL.
        """
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
