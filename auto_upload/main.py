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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("/tmp/auto_upload.log")],
)
logger = logging.getLogger("main")


def upload_to_platform(media_url, caption, platform):
    results = {}
    if platform in ("facebook", "both"):
        fb = FacebookUploader()
        try:
            results["facebook"] = fb.upload(media_url, caption)
            logger.info(f"Facebook upload succeeded")
        except Exception as e:
            results["facebook"] = f"FAILED: {e}"
            logger.error(f"Facebook upload failed: {e}")

    if platform in ("instagram", "both"):
        ig = InstagramUploader()
        try:
            results["instagram"] = ig.upload(media_url, caption)
            logger.info(f"Instagram upload succeeded")
        except Exception as e:
            results["instagram"] = f"FAILED: {e}"
            logger.error(f"Instagram upload failed: {e}")

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
            results = upload_to_platform(post["media_url"], post["caption"], post["platform"])

            fb_ok = "FAILED" not in str(results.get("facebook", ""))
            ig_ok = "FAILED" not in str(results.get("instagram", ""))

            if post["platform"] == "both":
                if fb_ok and ig_ok:
                    sheets.update_status(row, "uploaded", "Both platforms done")
                elif fb_ok:
                    sheets.update_status(row, "partial", "FB ok, IG failed")
                elif ig_ok:
                    sheets.update_status(row, "partial", "IG ok, FB failed")
                else:
                    sheets.update_status(row, "failed", "Both failed")
            else:
                ok = fb_ok if post["platform"] == "facebook" else ig_ok
                sheets.update_status(row, "uploaded" if ok else "failed", str(results))

            time.sleep(5)
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
