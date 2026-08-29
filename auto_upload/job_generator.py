import logging

logger = logging.getLogger(__name__)


def _is_clean_source(source):
    integrity_error = str(source.get("integrity_error", "") or "").strip()
    if integrity_error:
        return False, f"Source row integrity mismatch - {integrity_error}"
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


def _make_review_job(sku, account, reason):
    """A single needs_review placeholder job for sources that must not auto-publish."""
    return {
        "job_id": f"REVIEW-{sku}",
        "sku": sku,
        "account_id": account["account_id"],
        "media_selection": "review",
        "platform": "review",
        "format": "review",
        "language": account.get("primary_language", ""),
        "scheduled_at": "",
        "timezone": account.get("timezone", ""),
        "stock_id_tag": sku,
        "status": "needs_review",
        "attempts": 0,
        "last_attempt_at": "",
        "platform_post_id": "",
        "published_url": "",
        "error_message": "",
        "notes": reason,
        "tagging_status": "Pending",
        "tag_stock_id_used": "",
        "caption_final": "",
    }


def generate_jobs(sources, accounts):
    """Build upload jobs from clean Source Import rows for enabled accounts.

    Carousel + product Reel/video + one job for EACH model video, per the
    UPLOAD GUIDE format rules. Unclean rows (NON CERTIFIED, API error) are
    blocked from auto-publish and surfaced as a needs_review queue entry.
    """
    jobs = []
    for sku, source in sources.items():
        clean, reason = _is_clean_source(source)
        if not clean:
            logger.warning(f"SKU {sku}: blocked for auto-publish ({reason})")
            for account in accounts:
                if account.get("enabled"):
                    jobs.append(_make_review_job(sku, account, reason))
                    break
            continue

        has_carousel_media = bool(source["images"])

        for account in accounts:
            if not account.get("enabled"):
                continue
            platform = account.get("platform", "")
            account_id = account.get("account_id", "")

            if platform in ("facebook", "instagram", "line", "wechat", "pinterest", "x", "linkedin"):
                if has_carousel_media:
                    jobs.append(_make_job(sku, account_id, platform, "carousel", "carousel", account))
                if source["video_url"]:
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
                for i in range(len(source["model_videos"])):
                    jobs.append(_make_job(sku, account_id, platform, "video", f"model_video:{i}", account))
            elif platform in ("youtube", "tiktok", "twitch"):
                if source["video_url"]:
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
                for i in range(len(source["model_videos"])):
                    jobs.append(_make_job(sku, account_id, platform, "video", f"model_video:{i}", account))
            elif platform in ("shopee", "lazada"):
                if has_carousel_media:
                    jobs.append(_make_job(sku, account_id, platform, "carousel", "carousel", account))

    logger.info(f"Generated {len(jobs)} job(s)")
    return jobs
