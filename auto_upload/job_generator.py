import logging

logger = logging.getLogger(__name__)


def _is_clean_source(source):
    lab = source.get("lab", "").upper()
    if lab == "NON CERTIFIED":
        return False, "NON CERTIFIED - requires manual review"
    status = source.get("source_status", "").lower()
    if "error" in status or "429" in status or "api" in status:
        return False, "Source Status indicates API error - content may be incomplete"
    return True, ""


def _job_id(sku, account_id, media_selection):
    sel = media_selection.replace(":", "-").replace(" ", "-")
    return f"{sku}-{account_id}-{sel}"


def _make_job(sku, account_id, platform, fmt, media_selection, account):
    return {
        "job_id": _job_id(sku, account_id, media_selection),
        "sku": sku,
        "account_id": account_id,
        "media_selection": media_selection,
        "platform": platform,
        "format": fmt,
        "language": account.get("primary_language", ""),
        "scheduled_at": "",
        "timezone": account.get("timezone", ""),
        "stock_id_tag": sku,
        "status": "pending",
        "attempts": 0,
        "last_attempt_at": "",
        "platform_post_id": "",
        "published_url": "",
        "error_message": "",
        "notes": "",
        "tagging_status": "Pending",
        "tag_stock_id_used": "",
        "caption_final": "",
    }


def generate_jobs(sources, accounts):
    """Build upload jobs from clean Source Import rows for enabled accounts.

    Carousel + product Reel/video + one job for EACH model video, per the
    UPLOAD GUIDE format rules. Unclean rows (NON CERTIFIED, API error) are
    skipped with a review note.
    """
    jobs = []
    for sku, source in sources.items():
        clean, reason = _is_clean_source(source)
        if not clean:
            logger.warning(f"SKU {sku}: skipped ({reason})")
            continue

        has_carousel_media = bool(source["images"] or source["video_url"])

        for account in accounts:
            if not account.get("enabled"):
                continue
            platform = account.get("platform", "")
            account_id = account.get("account_id", "")

            if platform in ("facebook", "instagram"):
                if has_carousel_media:
                    jobs.append(_make_job(sku, account_id, platform, "carousel", "carousel", account))
                if source["video_url"]:
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
                for i in range(len(source["model_videos"])):
                    jobs.append(_make_job(sku, account_id, platform, "video", f"model_video:{i}", account))
            elif platform == "youtube":
                if source["video_url"]:
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
                for i in range(len(source["model_videos"])):
                    jobs.append(_make_job(sku, account_id, platform, "video", f"model_video:{i}", account))

    logger.info(f"Generated {len(jobs)} job(s)")
    return jobs
