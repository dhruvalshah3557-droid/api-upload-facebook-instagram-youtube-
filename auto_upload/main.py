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
from colourdiam_fetcher import ColourDiamFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")


def get_product_info(product_url):
    scraper = ProductScraper()
    return scraper.scrape(product_url)


def make_page_caption(caption, hashtags, product_info, page_name):
    if caption and hashtags:
        return f"{caption}\n\n{hashtags}"
    if caption:
        return caption
    auto_caption = generate_caption(product_info, page_name)
    auto_hashtags = generate_hashtags(product_info, page_name)
    return f"{auto_caption}\n\n{auto_hashtags}"


def upload_to_all_pages(media_url, caption, title, platform, product_url="", product_id=""):
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
                page_caption = make_page_caption(caption, "", product_info, name)

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
                    ig = InstagramUploader(ig_id, page_token, name)
                    try:
                        ig.upload(media_url, page_caption, product_id)
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
            yt.upload(media_url, title, caption)
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
                post["media_url"],
                post["caption"],
                post["title"],
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


def process_auto_pull():
    try:
        sheets = SheetsReader()
    except Exception as e:
        logger.error(f"Sheets connection failed: {e}")
        return

    posted = sheets.get_posted_ids()
    fetcher = ColourDiamFetcher()
    try:
        products = fetcher.get_featured_products()
    except Exception as e:
        logger.error(f"ColorDiam featured fetch failed: {e}")
        return

    if not products:
        logger.info("No featured products found")
        return

    max_posts = int(getattr(Config, "COLORDIAM_MAX_POSTS", "5"))
    logger.info(f"ColorDiam: {len(products)} featured product(s), {len(posted)} already posted, cap {max_posts}")
    posted_count = 0

    for product in products:
        if posted_count >= max_posts:
            logger.info(f"Reached ColorDiam cap of {max_posts} post(s)")
            break
        pid = product["id"]
        if pid in posted:
            logger.info(f"Product {pid} already posted, skipping")
            continue

        enriched = fetcher.enrich(product)
        media_url = ColourDiamFetcher.media_url(enriched)
        if not media_url:
            logger.warning(f"Product {pid} has no media, skipping")
            continue

        product_info = {
            "title": enriched.get("title", ""),
            "description": enriched.get("description", ""),
            "keywords": enriched.get("keywords", []),
        }
        caption, hashtags = sheets.get_caption_override(pid, product["url"])
        full_caption = make_page_caption(caption or "", hashtags or "", product_info, "colour diam")
        title = (enriched.get("title") or full_caption)[:100]

        try:
            results = upload_to_all_pages(
                media_url,
                full_caption,
                title,
                "all",
                product_url=product["url"],
                product_id=pid,
            )
            sheets.mark_posted(pid, results)
            posted_count += 1
            logger.info(f"Product {pid} posted: {results}")
        except Exception as e:
            logger.error(f"Product {pid} failed: {e}")
            time.sleep(10)


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
    elif mode == "--auto-pull":
        process_auto_pull()
    else:
        run_once()
