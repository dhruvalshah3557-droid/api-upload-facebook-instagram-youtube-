#!/usr/bin/env python3
import logging
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

from caption_generator import generate_caption, generate_hashtags
from config import Config
from facebook_uploader import FacebookUploader
from instagram_uploader import IGAccountNotLinkedError, InstagramUploader
from job_generator import generate_jobs
from line_uploader import LineUploader
from linkedin_uploader import LinkedInUploader
from media_prep import media_kind, validate_media_url
from pinterest_uploader import PinterestUploader
from shopee_uploader import ShopeeUploader
from sheets_reader import SheetsReader
from tiktok_uploader import TikTokUploader
from twitch_uploader import TwitchUploader
from wechat_uploader import WeChatUploader
from x_uploader import XUploader
from youtube_uploader import YouTubeUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")


_BROKEN_MEDIA_MARKERS = (
    "404 client error",
    "http error code 404",
    "404 not found",
    "media download has failed",
    "media could not be fetched",
    "could not be fetched from this uri",
    "video download failed with",
    "only photo or video can be accepted as media type",
    "media validation failed",
    "video transcoding error",
    "broken container",
    "dead links",
    "media urls are unavailable",
    "failed validation",
    "nothing to publish",
    "name or service not known",
    "failed to resolve",
    "name resolutionerror",
)


class DeliveryUncertainError(Exception):
    """The platform may have accepted a post; automatic retry is unsafe."""


def _is_broken_media_error(message):
    """Dead/broken media (404, un-fetchable URI) will not heal on retry."""
    lowered = message.lower()
    return any(marker in lowered for marker in _BROKEN_MEDIA_MARKERS)


_MEDIA_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; auto-upload)"}
_MEDIA_CLASS_CACHE = {}


def _classify_media_url(url):
    """Classify a media URL as 'image', 'video', 'invalid' or 'unknown'.

    Used to drop dead links before handing media to uploaders. The Instagram
    Graph API rejects carousel children whose URL does not return an actual
    image or video with "Only photo or video can be accepted as media type.".
    URLs that are unreachable or report no usable content type are treated as
    'unknown' (kept): the failure is likely transient and the uploader's
    normal error handling still applies.
    """
    url = str(url or "").strip()
    if not url:
        return "invalid"
    if url in _MEDIA_CLASS_CACHE:
        return _MEDIA_CLASS_CACHE[url]
    result = "unknown"
    try:
        resp = requests.get(
            url, headers=_MEDIA_HEADERS, timeout=20, stream=True, allow_redirects=True
        )
        content_type = (resp.headers.get("Content-Type") or "").lower().split(";", 1)[0]
        status = resp.status_code
        resp.close()
        if status not in (200, 206):
            result = "invalid"
        elif content_type.startswith("image/"):
            result = "image"
        elif content_type.startswith("video/"):
            result = "video"
        elif content_type in ("", "application/octet-stream"):
            result = "unknown"
        else:
            result = "invalid"
    except requests.RequestException:
        result = "unknown"
    _MEDIA_CLASS_CACHE[url] = result
    return result


def _filter_valid_media(media):
    """Drop media URLs that definitively do not point to an image/video.

    A single dead URL (404 HTML page, PDF, etc.) fails the whole post on the
    IG/FB APIs, so broken links are removed up front; if none of the resolved
    media is usable the job fails with a clear message instead of an opaque
    platform API error.
    """
    kept = []
    for url in media:
        if _classify_media_url(url) == "invalid":
            logger.warning(
                f"Media URL is not an accessible image/video, dropping from post: {url}"
            )
        else:
            kept.append(url)
    return kept


def _probe_job_media(job, media):
    """Strictly probe media that passed the content-type filter.

    _filter_valid_media only checks that a URL reports an image/video
    Content-Type. A truncated or corrupt video (e.g. an MP4 missing its moov
    box) still passes that check and then fails on the platform API with a
    "Video Transcoding Error / Broken container" that retries forever. When
    MEDIA_VALIDATION is enabled, videos are also probed with ffprobe and the
    job is raised as needs_review so it stops burning attempts.
    """
    if not Config.MEDIA_VALIDATION:
        return media
    strict = []
    for url in media:
        if media_kind(url) == "video":
            reason = validate_media_url(url, kind="video", ffprobe=True)
            if reason:
                raise Exception(f"Media validation failed ({url}): {reason}")
        strict.append(url)
    return strict


def open_sheets_with_retry():
    """Open Google Sheets robustly, including quota errors raised during init."""
    delays = (0, 15, 30, 60, 60, 60)
    last_error = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            logger.warning(f"Waiting {delay}s before retrying Google Sheets connection")
            time.sleep(delay)
        try:
            return SheetsReader()
        except Exception as exc:
            last_error = exc
            text = str(exc).lower()
            transient = "429" in text or "quota" in text or "500" in text or "503" in text
            if not transient or attempt == len(delays):
                raise
            logger.warning(
                f"Google Sheets connection attempt {attempt}/{len(delays)} failed: {exc}"
            )
    raise last_error


_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def _is_video_url(url):
    return any(ext in str(url or "").lower() for ext in _VIDEO_EXTS)


def _is_carousel_image_url(url):
    """Instagram carousel image URLs must point to an actual image, not a PDF."""
    lowered = str(url or "").split("?", 1)[0].lower()
    return bool(lowered) and not lowered.endswith(".pdf") and not _is_video_url(lowered)


def _dedupe_media(urls):
    ordered = []
    seen = set()
    for url in urls:
        url = str(url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def job_unique_key(job):
    """Unique key for a queue job: SKU + account + platform + format + media type.

    Guarantees the same job is never generated twice. The key must match
    SheetsReader.get_existing_job_keys() exactly.
    """
    return (
        job["sku"],
        job["account_id"],
        job["platform"],
        job["format"],
        job["media_selection"],
    )


def _lang_code(primary_language):
    return str(primary_language or "en").split("-")[0].lower()


def resolve_media(job, source):
    """Resolve ordered media URLs for one queue job.

    Carousels are image-only and ordered as:
      1) main product image
      2) certificate image on Instagram (when a public image URL is supplied)
      3) remaining product images
    Instagram accepts up to 10 carousel children, so its list is capped at 10.
    Product/model videos always remain separate video/Reel jobs.
    """
    selection = job.get("media_selection", "")
    if selection == "carousel":
        main_image = source.get("main_image", "")
        side_images = list(source.get("side_images", []))

        if job.get("platform", "").lower() == "instagram":
            # Keep the carousel image-only. Product/model videos are generated as
            # separate Reel jobs, so embedding the product video here creates a
            # duplicate post and makes one bad video fail an otherwise valid carousel.
            media = []
            if main_image:
                media.append(main_image)

            certificate_media = source.get("certificate_media_url", "")
            if certificate_media:
                if _is_carousel_image_url(certificate_media):
                    media.append(certificate_media)
                else:
                    logger.warning(
                        f"SKU {source.get('sku', '')}: certificate media is not a public image URL; "
                        "skipping it in the Instagram carousel"
                    )

            media.extend(side_images)
            return _dedupe_media(media)[:10]

        media = []
        if main_image:
            media.append(main_image)
        media.extend(side_images)
        return _dedupe_media(media)

    if selection == "product_video":
        return [source["video_url"]] if source["video_url"] else []
    if selection.startswith("model_video:"):
        try:
            idx = int(selection.split(":", 1)[1])
            videos = source["model_videos"]
            return [videos[idx]] if 0 <= idx < len(videos) else []
        except (ValueError, IndexError):
            return []
    return []


def _append_product_link(caption, source):
    caption = str(caption or "").strip()
    product_link = str(source.get("product_link", "") or "").strip()
    if not product_link or product_link in caption:
        return caption
    link_line = f"View product: {product_link}"
    return f"{caption}\n\n{link_line}" if caption else link_line


def build_caption(job, source, account):
    """Build a localized caption and always append the product page URL.

    Order: primary-language translation + matching hashtags -> fallback-language
    translation + matching hashtags -> platform caption + hashtags -> generated
    caption. The direct product page is then appended to every platform post.
    """
    platform = job.get("platform", "")
    lang = _lang_code(account.get("primary_language", ""))
    fallback = _lang_code(account.get("fallback_language", ""))

    lang_captions = source.get("lang_captions", {})
    lang_hashtags = source.get("lang_hashtags", {})
    hashtags = source.get("hashtags", "")

    caption_text = ""
    for code in (lang, fallback):
        if code and lang_captions.get(code):
            tags = lang_hashtags.get(code) or hashtags
            caption_text = (
                f"{lang_captions[code]}\n\n{tags}" if tags else lang_captions[code]
            )
            break

    if not caption_text:
        if platform in ("facebook", "wechat", "pinterest"):
            caption = source.get("facebook_caption", "")
        elif platform in ("instagram", "line"):
            caption = source.get("instagram_caption", "")
        elif platform == "youtube":
            caption = source.get("youtube_shorts_caption", "") or source.get("facebook_caption", "")
        elif platform in ("tiktok", "x", "twitch", "shopee", "lazada"):
            caption = source.get("instagram_caption", "") or source.get("facebook_caption", "")
        elif platform == "linkedin":
            caption = source.get("facebook_caption", "") or source.get("instagram_caption", "")
        else:
            caption = source.get("instagram_caption", "")

        if caption and hashtags:
            caption_text = f"{caption}\n\n{hashtags}"
        elif caption:
            caption_text = caption

    if not caption_text:
        product_info = {
            "title": source.get("product_name", "Diamond Jewelry"),
            "description": source.get("product_name", ""),
            "keywords": [source.get("product_name", "")],
        }
        auto_caption = generate_caption(product_info, account.get("account_name", ""))
        auto_hashtags = generate_hashtags(product_info, account.get("account_name", ""))
        caption_text = f"{auto_caption}\n\n{auto_hashtags}"

    return _append_product_link(caption_text, source)


def _carousel_images(media):
    return [u for u in media if not _is_video_url(u)]


def _facebook_post_url(page_id, post_id):
    """Fallback FB post URL when the Graph permalink lookup fails."""
    numeric = str(post_id).rsplit("_", 1)[-1]
    return f"https://www.facebook.com/{page_id}/posts/{numeric}"


def _tag_value(job):
    return job.get("stock_id_tag", "") or job.get("sku", "")


def publish_job(job, source, account):
    """Publish one job and return (post_id, published_url)."""
    media = resolve_media(job, source)
    if not media:
        raise Exception("No media resolved for job")

    platform = job.get("platform", "")
    format_type = job.get("format", "")
    caption = build_caption(job, source, account)

    media = _filter_valid_media(media)
    if not media:
        raise Exception("All resolved media URLs are unavailable (404/dead links); nothing to publish")

    media = _probe_job_media(job, media)
    if not media:
        raise Exception("All resolved media URLs failed validation; nothing to publish")

    tag = _tag_value(job) if account.get("product_tagging") else ""
    # Instagram product tagging needs a real product item ID from a connected
    # catalog. Sending the raw SKU as product_id makes the whole post fail with
    # "(#100) ... not a valid product item ID", so fall back to untagged posts
    # until a catalog is configured for the account.
    if platform == "instagram" and tag and not account.get("catalog_or_store_id"):
        tag = ""

    if platform == "facebook":
        token = Config.get_token(account.get("credential_property_key", ""))
        if not token:
            raise Exception("No access token configured for this account")
        uploader = FacebookUploader(account["platform_account_id"], token, account.get("account_name", ""))
        if format_type == "carousel":
            images = _carousel_images(media)
            if not images:
                post = uploader.upload(media[0], caption, tag)
            else:
                post = uploader.upload_carousel(images, caption, tag)
        else:
            post = uploader.upload(media[0], caption, tag)
        post_id = post.get("id", "")
        url = uploader.permalink_url(post_id) or _facebook_post_url(
            account["platform_account_id"], post_id
        )
        return post_id, url

    if platform == "instagram":
        token = Config.get_token(account.get("credential_property_key", ""))
        if not token:
            raise Exception("No access token configured for this account")
        uploader = InstagramUploader(account["platform_account_id"], token, account.get("account_name", ""))
        if format_type == "carousel":
            post = uploader.upload_carousel(media, caption, tag)
        else:
            post = uploader.upload(media[0], caption, tag)
        post_id = post.get("id", "")
        url = uploader.permalink(post_id) or f"https://www.instagram.com/p/{post_id}"
        return post_id, url

    if platform == "youtube":
        yt_key = account.get("credential_property_key", "") or "YOUTUBE_OAUTH_REFRESH_TOKEN"
        yt_token = os.getenv(yt_key) or os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN")
        if not yt_token:
            raise Exception(
                "No YouTube OAuth refresh token configured "
                f"(set {yt_key} / YOUTUBE_OAUTH_REFRESH_TOKEN)"
            )
        if account.get("credential_property_key") and not os.getenv(yt_key):
            raise Exception(
                f"No YouTube OAuth refresh token configured for {yt_key} "
                "(a per-account token must be set; refusing to fall back to the shared token)"
            )
        uploader = YouTubeUploader(refresh_token=yt_token)
        title = (job.get("title") or source.get("product_name") or "Video")[:100]
        description = caption
        response = uploader.upload(media[0], title, description)
        post_id = response.get("id", "")
        url = f"https://youtu.be/{post_id}"
        return post_id, url

    if platform == "line":
        line_token = os.getenv(account.get("credential_property_key", "")) or os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
        if not line_token:
            raise Exception("No LINE channel access token configured (set LINE_CHANNEL_ACCESS_TOKEN)")
        uploader = LineUploader(line_token, account.get("account_name", ""))
        if format_type == "carousel":
            images = _carousel_images(media)
            post = uploader.upload_carousel(images or media, caption)
        else:
            is_video = format_type == "video" or any(
                ext in str(media[0]).lower() for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm")
            )
            thumbnail = source.get("main_image", "") if is_video else ""
            if is_video and not thumbnail:
                images = list(source.get("images") or []) + list(source.get("side_images") or [])
                thumbnail = next((u for u in images if u), "")
            post = uploader.upload(media[0], caption, is_video=is_video, thumbnail_url=thumbnail)
        post_id = post.get("id", "")
        url = post.get("url", "https://line.me/")
        return post_id, url

    if platform == "wechat":
        wechat_cred = os.getenv(account.get("credential_property_key", ""))
        uploader = WeChatUploader(
            credential=wechat_cred,
            account_name=account.get("account_name", ""),
        )
        title = (job.get("title") or source.get("product_name") or "Product")[:64]
        if format_type == "carousel":
            images = _carousel_images(media)
            post = uploader.upload_carousel(images or media, caption, title)
        else:
            is_video = format_type == "video"
            post = uploader.upload(media[0], caption, title=title, is_video=is_video)
        post_id = post.get("id", "")
        url = post.get("url", "https://mp.weixin.qq.com/")
        return post_id, url

    if platform == "pinterest":
        pin_token = os.getenv(account.get("credential_property_key", "")) or os.getenv("PINTEREST_ACCESS_TOKEN")
        uploader = PinterestUploader(
            access_token=pin_token,
            board_id=account.get("platform_account_id", ""),
            account_name=account.get("account_name", ""),
        )
        title = (job.get("title") or source.get("product_name") or "Product")[:100]
        product_link = source.get("product_link", "")
        if format_type == "carousel":
            images = _carousel_images(media)
            post = uploader.upload_carousel(images or media, caption, title, product_link)
        else:
            is_video = format_type == "video"
            thumbnail = source.get("main_image", "") if is_video else ""
            post = uploader.upload(media[0], caption, title=title, link=product_link,
                                   thumbnail_url=thumbnail, is_video=is_video)
        post_id = post.get("id", "")
        url = post.get("url", f"https://www.pinterest.com/pin/{post_id}/")
        return post_id, url

    if platform == "tiktok":
        tiktok_token = os.getenv(account.get("credential_property_key", "")) or os.getenv("TIKTOK_ACCESS_TOKEN")
        uploader = TikTokUploader(tiktok_token, account.get("account_name", ""))
        title = (job.get("title") or source.get("product_name") or "Video")[:150]
        post = uploader.upload(media[0], title=title, description=caption)
        post_id = post.get("id", "")
        url = post.get("url", f"https://www.tiktok.com/@{account.get('account_name', '')}/video/{post_id}")
        return post_id, url

    if platform == "twitch":
        if format_type != "video":
            raise Exception("Twitch only supports video jobs (carousel/image posts are not available on Twitch)")
        twitch_key = os.getenv(account.get("credential_property_key", "")) or os.getenv("TWITCH_STREAM_KEY")
        if not twitch_key:
            raise Exception("No Twitch stream key configured (set TWITCH_STREAM_KEY or META_TOKEN_TWITCH_*)")
        uploader = TwitchUploader(
            stream_key=twitch_key,
            broadcaster_id=account.get("platform_account_id", ""),
            account_name=account.get("account_name", ""),
        )
        title = (job.get("title") or source.get("product_name") or "Video")[:140]
        post = uploader.upload(media[0], title=title, description=caption)
        post_id = post.get("id", "")
        channel = account.get("username_or_channel", "") or account.get("account_name", "")
        url = post.get("url") or f"https://www.twitch.tv/{channel}"
        return post_id, url

    if platform == "shopee":
        if format_type != "carousel":
            raise Exception("Shopee media upload supports image/carousel jobs only")
        uploader = ShopeeUploader(account_name=account.get("account_name", ""))
        images = _carousel_images(media)
        title = (job.get("title") or source.get("product_name") or "Product")[:100]
        results = uploader.upload_carousel(images or media, caption, title)
        first = results[0] if results else {}
        post_id = first.get("id", "")
        url = first.get("url", "")
        return post_id, url

    if platform == "lazada":
        if format_type != "carousel":
            raise Exception("Lazada media upload supports image/carousel jobs only")
        uploader = LazadaUploader(account_name=account.get("account_name", ""))
        images = _carousel_images(media)
        title = (job.get("title") or source.get("product_name") or "Product")[:100]
        results = uploader.upload_carousel(images or media, caption, title)
        first = results[0] if results else {}
        post_id = first.get("id", "")
        url = first.get("url", "")
        return post_id, url

    if platform == "x":
        uploader = XUploader(account_name=account.get("account_name", ""))
        title = (job.get("title") or source.get("product_name") or "Video")[:280]
        if format_type == "carousel":
            images = _carousel_images(media)
            post = uploader.upload_carousel(images or media, caption)
        else:
            is_video = format_type == "video"
            post = uploader.upload(media[0], caption, title=title, is_video=is_video)
        post_id = post.get("id", "")
        url = post.get("url", f"https://x.com/i/status/{post_id}")
        return post_id, url

    if platform == "linkedin":
        li_token = os.getenv(account.get("credential_property_key", "")) or os.getenv("LINKEDIN_ACCESS_TOKEN")
        uploader = LinkedInUploader(
            access_token=li_token,
            author=account.get("platform_account_id", ""),
            account_name=account.get("account_name", ""),
        )
        title = (job.get("title") or source.get("product_name") or "Video")[:100]
        if format_type == "carousel":
            images = _carousel_images(media)
            post = uploader.upload_carousel(images or media, caption)
        else:
            is_video = format_type == "video"
            post = uploader.upload(media[0], caption, title=title, is_video=is_video)
        post_id = post.get("id", "")
        url = post.get("url", f"https://www.linkedin.com/feed/update/urn:li:activity:{post_id}")
        return post_id, url

    raise Exception(f"Unsupported platform: {platform}")


def read_upload_guide(sheets):
    """Read the UPLOAD GUIDE before uploading and log its safety rules."""
    guide_rows = sheets.get_upload_guide()
    if not guide_rows:
        logger.error(f"UPLOAD GUIDE could not be read: {sheets.guide_error or 'tab empty'}")
        return []
    logger.info(f"UPLOAD GUIDE loaded ({len(guide_rows)} rows) before upload")
    for rule in sheets.guide_safety_rules(guide_rows):
        logger.info(f"[UPLOAD GUIDE] {rule}")
    return guide_rows


def insert_logs_newest_first(sheets, entries):
    """Write log entries under the header so the newest result is always on top."""
    if not entries:
        return
    rows = [
        [entry.get(col, "") for col in sheets.LOG_COLS]
        for entry in reversed(entries)
    ]
    for attempt, delay in enumerate((0, 15, 30, 60), start=1):
        if delay:
            time.sleep(delay)
        try:
            sheets.log_ws.insert_rows(
                rows, row=sheets.log_header_row + 1, value_input_option="USER_ENTERED"
            )
            return
        except Exception as exc:
            text = str(exc).lower()
            if ("429" not in text and "quota" not in text) or attempt == 4:
                raise
            logger.warning(f"Publishing Log insert hit quota; retrying: {exc}")


def _round_robin_jobs(jobs, limit):
    """Select up to `limit` jobs, alternating across platforms.

    Without this, FIFO selection lets one platform's backlog (e.g. a large FB
    pending backlog sitting below newer IG/YT rows) starve the others, so
    those platforms never publish and produce no log entries.
    """
    by_platform = {}
    order = []
    for job in jobs:
        platform = job.get("platform", "")
        if platform not in by_platform:
            by_platform[platform] = []
            order.append(platform)
        by_platform[platform].append(job)
    selected = []
    while len(selected) < limit and any(by_platform.values()):
        for platform in order:
            if len(selected) >= limit:
                break
            if by_platform[platform]:
                selected.append(by_platform[platform].pop(0))
    return selected


def process_pending(sheets=None):
    try:
        sheets = sheets or open_sheets_with_retry()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return

    read_upload_guide(sheets)

    accounts = {a["account_id"]: a for a in sheets.get_accounts()}
    sources = sheets.get_source_rows()
    jobs = sheets.get_pending_jobs()
    if not jobs:
        logger.info("No pending jobs")
        return

    logger.info(f"Processing {len(jobs)} pending job(s)")
    log_buffer = []
    try:
        for job in _round_robin_jobs(jobs, Config.MAX_JOBS_PER_RUN):
            job_id = job["job_id"]
            logger.info(f"Job {job_id}: {job['platform']}/{job['format']} - {job['media_selection']} (SKU {job['sku']})")

            account = accounts.get(job["account_id"])
            if not account or not account.get("enabled"):
                sheets.update_job(job, {"status": Config.JOB_STATUS_SKIPPED, "notes": "Account disabled or missing"})
                log_buffer.append(sheets.log_entry(job, "skipped", "Account disabled or missing"))
                logger.warning(f"Job {job_id}: skipped - account not enabled")
                continue

            source = sources.get(job["sku"])
            if not source:
                sheets.update_job(job, {"status": Config.JOB_STATUS_SKIPPED, "notes": "SKU not found in Source Import"})
                log_buffer.append(sheets.log_entry(job, "skipped", "SKU not found in Source Import"))
                logger.warning(f"Job {job_id}: skipped - SKU {job['sku']} missing")
                continue

            caption = build_caption(job, source, account)
            post_id, url = "", ""
            try:
                post_id, url = publish_job(job, source, account)
                updates = {
                    "status": Config.JOB_STATUS_UPLOADED,
                    "platform_post_id": post_id,
                    "published_url": url,
                    "caption_final": caption,
                    "tag_stock_id_used": _tag_value(job),
                    "tagging_status": "Tagged" if account.get("product_tagging") else "Unavailable",
                    "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "error_message": "",
                }
                sheets.update_job(job, updates)
                log_buffer.append(sheets.log_entry(
                    {**job, "platform_post_id": post_id, "published_url": url},
                    "success",
                ))
                logger.info(f"Job {job_id}: uploaded -> {url}")
            except DeliveryUncertainError as e:
                message = str(e)
                sheets.update_job(job, {
                    "status": "hold",
                    "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "error_message": message[:2000],
                    "notes": (
                        f"{job.get('notes', '')} | DELIVERY_UNCERTAIN: "
                        "platform may have accepted the post; do not auto-retry"
                    ).strip(" |"),
                })
                log_buffer.append(sheets.log_entry(job, "uncertain", message))
                logger.error(
                    f"Job {job_id}: delivery uncertain; held to prevent duplicate: {message}"
                )
            except IGAccountNotLinkedError as e:
                sheets.update_job(job, {
                    "status": "pending",
                    "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "error_message": str(e)[:2000],
                    "notes": "IG business account not linked yet; waiting to retry",
                })
                logger.warning(
                    f"Job {job_id}: IG business account not linked, "
                    f"keeping pending (no attempt consumed): {e}"
                )
            except Exception as e:
                message = str(e)
                api_code = ""
                if "error" in message.lower():
                    try:
                        api_code = message.split("(")[-1].rstrip(")").split(" ")[0]
                    except Exception:
                        api_code = ""
                if post_id:
                    attempts = int(job.get("attempts", 0) or 0)
                    status = Config.JOB_STATUS_NEEDS_REVIEW
                    updates = {
                        "status": status,
                        "attempts": attempts,
                        "platform_post_id": post_id,
                        "published_url": url,
                        "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "error_message": (
                            "Platform publish succeeded but final Sheets update failed: "
                            + message
                        )[:2000],
                        "notes": "DELIVERY_CONFIRMED_DO_NOT_RETRY; reconcile queue/log manually",
                    }
                else:
                    attempts = job.get("attempts", 0) + 1
                    if _is_broken_media_error(message):
                        status = Config.JOB_STATUS_NEEDS_REVIEW
                    else:
                        status = (
                            Config.JOB_STATUS_FAILED
                            if attempts >= Config.MAX_JOB_ATTEMPTS else "pending"
                        )
                    updates = {
                        "status": status,
                        "attempts": attempts,
                        "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "error_message": message[:2000],
                        "tagging_status": "Failed"
                            if (status == Config.JOB_STATUS_FAILED and account.get("product_tagging"))
                            else job.get("tagging_status", "Pending"),
                    }
                sheets.update_job(job, updates)
                log_buffer.append(sheets.log_entry(job, "failed", message, api_code))
                logger.error(f"Job {job_id}: failed ({status}): {message}")

            time.sleep(10)
    finally:
        if log_buffer:
            insert_logs_newest_first(sheets, log_buffer)
            logger.info(
                f"Inserted {len(log_buffer)} entr(y/ies) at top of Publishing Log"
            )


def run_generate(sheets=None):
    try:
        sheets = sheets or open_sheets_with_retry()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return
    read_upload_guide(sheets)
    accounts = sheets.get_accounts()
    sources = sheets.get_source_rows()
    existing = sheets.get_existing_job_keys()
    # Accounts with no queue history must not wait behind destinations that
    # already have thousands of jobs. Snapshot counts before adding missing
    # keys so empty/new queues (including LINE) are filled first.
    existing_counts = {}
    for key in existing:
        account_id = str(key[1] or "review")
        existing_counts[account_id] = existing_counts.get(account_id, 0) + 1

    missing_by_account = {}
    already_present = 0
    for job in generate_jobs(sources, accounts):
        key = job_unique_key(job)
        if key in existing:
            already_present += 1
            continue
        existing.add(key)
        account_id = str(job.get("account_id", "") or "review")
        missing_by_account.setdefault(account_id, []).append(job)

    # Select missing work fairly. The former first-N loop repeatedly exhausted
    # its cap on early Accounts rows, so newly added destinations such as Spain,
    # Italy, Vietnam, Pakistan, Kuwait and Dubai never received queue entries.
    # Rotate account windows each 10-minute production slot, then take one job
    # per account per pass until the generation cap is full.
    account_ids = list(missing_by_account)
    if account_ids:
        slot = int(time.time() // 600)
        start = (slot * max(1, Config.MAX_GENERATE_JOBS)) % len(account_ids)
        account_ids = account_ids[start:] + account_ids[:start]
        rotation_rank = {account_id: i for i, account_id in enumerate(account_ids)}
        account_ids.sort(
            key=lambda account_id: (
                existing_counts.get(account_id, 0) > 0,
                existing_counts.get(account_id, 0),
                rotation_rank[account_id],
            )
        )

    new_jobs = []
    while account_ids and len(new_jobs) < Config.MAX_GENERATE_JOBS:
        next_pass = []
        for account_id in account_ids:
            bucket = missing_by_account[account_id]
            if bucket and len(new_jobs) < Config.MAX_GENERATE_JOBS:
                new_jobs.append(bucket.pop(0))
            if bucket:
                next_pass.append(account_id)
        account_ids = next_pass

    remaining = sum(len(bucket) for bucket in missing_by_account.values())
    if remaining:
        logger.info(
            "Generation cap reached (%s); %s missing job(s) remain for later fair rotations",
            Config.MAX_GENERATE_JOBS, remaining,
        )
    sheets.append_jobs(new_jobs)
    logger.info(
        f"Queue generation: {len(new_jobs)} new job(s) appended, "
        f"{already_present} already queued (skipped)"
    )


def run_cycle():
    """Generate missing jobs and upload pending jobs using ONE Sheets connection."""
    logger.info("=== Auto Upload: Generate + Upload Cycle ===")
    try:
        sheets = open_sheets_with_retry()
    except Exception as e:
        logger.error(f"Sheets connection failed after retries: {e}")
        return
    run_generate(sheets)
    # Reuse the same workbook/worksheet objects instead of reconnecting. Give
    # Google's per-minute read quota a fresh window before the publish phase.
    logger.info("Generation complete; waiting 65s for Sheets quota window before upload")
    time.sleep(65)
    process_pending(sheets)


def run_once():
    logger.info("=== Auto Upload: Single Run ===")
    process_pending()


def run_loop():
    interval = 300
    logger.info(f"=== Auto Upload: Loop every {interval}s ===")
    while True:
        process_pending()
        time.sleep(interval)


_PLATFORM_TARGETS = {
    "both": {"facebook", "instagram"},
    "all": {"facebook", "instagram", "youtube"},
}


def run_direct(media_url, caption, platform, product_url="", product_id=""):
    logger.info(f"=== Auto Upload: Direct Upload ({platform}) {media_url} ===")
    if not media_url:
        logger.error("No media URL provided")
        return
    is_video = any(e in media_url.lower() for e in _VIDEO_EXTS)
    target_platforms = _PLATFORM_TARGETS.get(platform, {platform})
    try:
        sheets = open_sheets_with_retry()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return
    read_upload_guide(sheets)
    accounts = [a for a in sheets.get_accounts() if a.get("enabled") and a.get("platform") in target_platforms]
    if not accounts:
        logger.warning("No enabled accounts for this platform")
        return
    for account in accounts:
        job = {
            "job_id": f"DIRECT-{account['platform']}-{int(time.time())}",
            "sku": product_id or "direct",
            "account_id": account["account_id"],
            "media_selection": "product_video" if is_video else "carousel",
            "platform": account["platform"],
            "format": "video" if is_video else "carousel",
            "stock_id_tag": product_id or "",
            "language": account.get("primary_language", ""),
        }
        source = {
            "sku": product_id or "direct",
            "video_url": media_url,
            "main_image": "" if is_video else media_url,
            "side_images": [],
            "images": [media_url],
            "model_videos": [],
            "certificate_media_url": "",
            "product_link": product_url or "",
            "product_name": (caption or "Video")[:100],
            "facebook_caption": caption or "",
            "instagram_caption": caption or "",
            "youtube_shorts_caption": caption or "",
            "hashtags": "",
            "lang_captions": {},
            "lang_hashtags": {},
        }
        try:
            post_id, url = publish_job(job, source, account)
            logger.info(f"[{account['account_id']}] direct upload -> {url}")
        except Exception as e:
            logger.error(f"[{account['account_id']}] direct upload failed: {e}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "--loop":
        run_loop()
    elif mode == "--generate":
        run_generate()
    elif mode == "--cycle":
        run_cycle()
    elif mode == "--direct":
        run_direct(
            os.getenv("MEDIA_URL", ""),
            os.getenv("CAPTION", ""),
            os.getenv("PLATFORM", "both").lower(),
            os.getenv("PRODUCT_URL", ""),
            os.getenv("PRODUCT_ID", ""),
        )
    else:
        run_once()
