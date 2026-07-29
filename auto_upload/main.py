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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")


def upload_to_all_pages(media_url, caption, title, platform):
    pages = Config.get_pages()
    results = {}

    should_fb = platform in ("facebook", "both", "all")
    should_ig = platform in ("instagram", "both", "all")
    should_yt = platform in ("youtube", "all")

    if should_fb or should_ig:
        if not pages:
            logger.error("No Facebook pages found. Check FB_ACCESS_TOKEN.")
            results["pages_error"] = "No pages found"
        else:
            logger.info(f"Uploading to {len(pages)} page(s) for FB/IG")
            for page in pages:
                name = page["name"]
                page_id = page["page_id"]
                page_token = page["page_token"]
                ig_id = page.get("ig_user_id", "")

                if should_fb:
                    fb = FacebookUploader(page_id, page_token, name)
                    try:
                        fb.upload(media_url, caption)
                        results[f"{name}_fb"] = "ok"
                        logger.info(f"[{name}] Facebook done")
                    except Exception as e:
                        results[f"{name}_fb"] = str(e)
                        logger.error(f"[{name}] Facebook failed: {e}")

                if should_ig and ig_id:
                    ig = InstagramUploader(ig_id, page_token, name)
                    try:
                        ig.upload(media_url, caption)
                        results[f"{name}_ig"] = "ok"
                        logger.info(f"[{name}] Instagram done")
                    except Exception as e:
                        results[f"{name}_ig"] = str(e)
                        logger.error(f"[{name}] Instagram failed: {e}")
                elif should_ig and not ig_id:
                    logger.warning(f"[{name}] No Instagram account linked, skipping IG")

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
            results = upload_to_all_pages(post["media_url"], post["caption"], post["title"], post["platform"])
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
