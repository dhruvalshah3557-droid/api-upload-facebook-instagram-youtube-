#!/usr/bin/env python3
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from caption_generator import generate_caption, generate_hashtags
from config import Config
from facebook_uploader import FacebookUploader
from instagram_uploader import InstagramUploader
from job_generator import generate_jobs
from sheets_reader import SheetsReader
from youtube_uploader import YouTubeUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


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
    """Resolve the ordered media URLs for a job's media_selection.

    Carousels are IMAGES ONLY (MAIN center image -> side images). The product
    video is never included: it is published separately as its own Reel/video
    job to avoid duplicate posts.
    """
    selection = job.get("media_selection", "")
    if selection == "carousel":
        media = []
        if source["main_image"]:
            media.append(source["main_image"])
        media.extend(source["side_images"])
        return media
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


def build_caption(job, source, account):
    """Caption precedence enforcing account language.

    Order: primary_language translation + matching hashtags -> fallback_language
    translation + matching hashtags (explicitly allowed) -> platform caption +
    hashtags. Caption and hashtags always come from the same language so posts
    never mix languages.
    """
    platform = job.get("platform", "")
    lang = _lang_code(account.get("primary_language", ""))
    fallback = _lang_code(account.get("fallback_language", ""))

    lang_captions = source.get("lang_captions", {})
    lang_hashtags = source.get("lang_hashtags", {})
    hashtags = source.get("hashtags", "")

    for code in (lang, fallback):
        if code and lang_captions.get(code):
            tags = lang_hashtags.get(code) or hashtags
            return f"{lang_captions[code]}\n\n{tags}" if tags else lang_captions[code]

    if platform == "facebook":
        caption = source.get("facebook_caption", "")
    elif platform == "instagram":
        caption = source.get("instagram_caption", "")
    elif platform == "youtube":
        caption = source.get("youtube_shorts_caption", "") or source.get("facebook_caption", "")
    else:
        caption = source.get("instagram_caption", "")

    if caption and hashtags:
        return f"{caption}\n\n{hashtags}"
    if caption:
        return caption

    product_info = {
        "title": source.get("product_name", "Diamond Jewelry"),
        "description": source.get("product_name", ""),
        "keywords": [source.get("product_name", "")],
    }
    auto_caption = generate_caption(product_info, account.get("account_name", ""))
    auto_hashtags = generate_hashtags(product_info, account.get("account_name", ""))
    return f"{auto_caption}\n\n{auto_hashtags}"


def _carousel_images(media):
    return [u for u in media if not any(ext in u.lower() for ext in _VIDEO_EXTS)]


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
    tag = _tag_value(job) if account.get("product_tagging") else ""

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
        url = f"https://www.facebook.com/{account['platform_account_id']}/posts/{post_id}"
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
        url = f"https://www.instagram.com/p/{post_id}"
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
        product_link = source.get("product_link", "")
        if product_link:
            description = f"{description}\n\nProduct: {product_link}" if description else f"Product: {product_link}"
        response = uploader.upload(media[0], title, description)
        post_id = response.get("id", "")
        url = f"https://youtu.be/{post_id}"
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


def process_pending(sheets=None):
    try:
        sheets = sheets or SheetsReader()
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
        for job in jobs[:Config.MAX_JOBS_PER_RUN]:
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
            except Exception as e:
                attempts = job.get("attempts", 0) + 1
                status = Config.JOB_STATUS_FAILED if attempts >= Config.MAX_JOB_ATTEMPTS else "pending"
                message = str(e)
                api_code = ""
                if "error" in message.lower():
                    try:
                        api_code = message.split("(")[-1].rstrip(")").split(" ")[0]
                    except Exception:
                        api_code = ""
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
            sheets.append_logs(log_buffer)
            logger.info(f"Appended {len(log_buffer)} entr(y/ies) to Publishing Log")


def run_generate(sheets=None):
    try:
        sheets = sheets or SheetsReader()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return
    read_upload_guide(sheets)
    accounts = sheets.get_accounts()
    sources = sheets.get_source_rows()
    existing = sheets.get_existing_job_keys()
    new_jobs = []
    already_present = 0
    for job in generate_jobs(sources, accounts):
        key = job_unique_key(job)
        if key in existing:
            already_present += 1
            continue
        new_jobs.append(job)
        existing.add(key)
        if len(new_jobs) >= Config.MAX_GENERATE_JOBS:
            logger.info(
                f"Generation cap reached ({Config.MAX_GENERATE_JOBS}); "
                f"remaining jobs queued on later runs"
            )
            break
    sheets.append_jobs(new_jobs)
    logger.info(
        f"Queue generation: {len(new_jobs)} new job(s) appended, "
        f"{already_present} already queued (skipped)"
    )


def run_once():
    logger.info("=== Auto Upload: Single Run ===")
    process_pending()


def run_loop():
    interval = 300
    logger.info(f"=== Auto Upload: Loop every {interval}s ===")
    while True:
        process_pending()
        time.sleep(interval)


def run_direct(media_url, caption, platform, product_url="", product_id=""):
    logger.info(f"=== Auto Upload: Direct Upload ({platform}) {media_url} ===")
    if not media_url:
        logger.error("No media URL provided")
        return
    try:
        sheets = SheetsReader()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return
    read_upload_guide(sheets)
    accounts = [a for a in sheets.get_accounts() if a.get("enabled") and a.get("platform") == platform]
    if not accounts:
        logger.warning("No enabled accounts for this platform")
        return
    for account in accounts:
        job = {
            "job_id": f"DIRECT-{platform}-{int(time.time())}",
            "sku": product_id or "direct",
            "account_id": account["account_id"],
            "media_selection": "product_video" if any(e in media_url.lower() for e in _VIDEO_EXTS) else "carousel",
            "platform": platform,
            "format": "carousel",
            "stock_id_tag": product_id or "",
            "language": account.get("primary_language", ""),
        }
        source = {
            "video_url": media_url,
            "main_image": "" if any(e in media_url.lower() for e in _VIDEO_EXTS) else media_url,
            "side_images": [],
            "images": [media_url],
            "model_videos": [],
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
