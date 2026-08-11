#!/usr/bin/env python3
import os
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from sheets_reader import SheetsReader
from facebook_uploader import FacebookUploader
from instagram_uploader import InstagramUploader
from youtube_uploader import YouTubeUploader
from product_scraper import ProductScraper
from caption_generator import generate_caption, generate_hashtags

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")


def get_product_info(product_url):
    scraper = ProductScraper()
    return scraper.scrape(product_url)


def make_page_caption(post, platform, page_name, product_info):
    lang = get_lang(page_name)
    lang_caption = post.get("lang_captions", {}).get(lang, "")
    lang_hashtags = post.get("lang_hashtags", {}).get(lang, "")
    hashtags = post.get("hashtags", "")

    if lang_caption:
        tags = lang_hashtags or hashtags
        return f"{lang_caption}\n\n{tags}" if tags else lang_caption

    caption = post.get("facebook_caption", "") if platform == "facebook" else post.get("instagram_caption", "")
    if caption and hashtags:
        return f"{caption}\n\n{hashtags}"
    if caption:
        return caption
    auto_caption = generate_caption(product_info, page_name)
    auto_hashtags = generate_hashtags(product_info, page_name)
    return f"{auto_caption}\n\n{auto_hashtags}"


def upload_to_all_pages(post, platform, product_url="", product_id=""):
    media_url = post["media_url"]
    pages = Config.get_pages()
    results = {}

    should_fb = platform in ("facebook", "both", "all")
    should_ig = platform in ("instagram", "both", "all")
    should_yt = platform in ("youtube", "all")

    product_info = get_product_info(product_url) if product_url else {"title": "", "description": "", "keywords": []}

    if should_fb or should_ig:
        if not pages:
            logger.error("No Facebook pages found.")
            results["pages_error"] = "No pages found"
        else:
            logger.info(f"Uploading to {len(pages)} page(s)")
            for page in pages:
                name = page["name"]
                page_id = page["page_id"]
                page_token = page["page_token"]
                ig_id = page.get("ig_user_id", "")
                page_caption = make_page_caption(post, "facebook", name, product_info)

                if should_fb:
                    fb = FacebookUploader(page_id, page_token, name)
                    try:
                        fb.upload(media_url, page_caption, product_id)
                        results[f"{name}_fb"] = "ok"
                        logger.info(f"[{name}] Facebook done")
                    except Exception as e:
                        results[f"{name}_fb"] = str(e)
                        logger.error(f"[{name}] Facebook failed: {e}")

                if should_ig and ig_id:
                    ig_caption = make_page_caption(post, "instagram", name, product_info)
                    ig = InstagramUploader(ig_id, page_token, name)
                    try:
                        ig.upload(media_url, ig_caption, product_id)
                        results[f"{name}_ig"] = "ok"
                        logger.info(f"[{name}] Instagram done")
                    except Exception as e:
                        results[f"{name}_ig"] = str(e)
                        logger.error(f"[{name}] Instagram failed: {e}")
                elif should_ig and not ig_id:
                    logger.warning(f"[{name}] No Instagram linked, skipping IG")

    if should_yt:
        try:
            yt = YouTubeUploader()
            yt_caption = post.get("youtube_caption", "") or post.get("facebook_caption", "")
            yt.upload(media_url, post.get("title", "Video"), yt_caption)
            results["youtube"] = "ok"
            logger.info("YouTube done")
        except Exception as e:
            results["youtube"] = str(e)
            logger.error(f"YouTube failed: {e}")

    return results


def process_pending():
    try:
        sheets = SheetsReader()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return

    pending = sheets.get_pending_posts()
    if not pending:
        logger.info("No pending posts")
        return

    logger.info(f"Processing {len(pending)} pending post(s)")
    for post in pending:
        row = post["row"]
        logger.info(f"Row {row}: {post['platform']} - {post['media_url']}")
        try:
            results = upload_to_all_pages(
                post,
                post["platform"],
                post.get("product_url", ""),
                post.get("product_id", ""),
            )
            sheets.update_status(row, "uploaded", str(results))
            time.sleep(10)
        except Exception as e:
            logger.error(f"Row {row} failed: {e}")
            sheets.update_status(row, "failed", str(e))


def run_once():
    logger.info("=== Auto Upload: Single Run ===")
    process_pending()


def run_loop():
    interval = 300
    logger.info(f"=== Auto Upload: Loop every {interval}s ===")
    while True:
        process_pending()
        time.sleep(interval)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "--loop":
        run_loop()
    else:
        run_once()
