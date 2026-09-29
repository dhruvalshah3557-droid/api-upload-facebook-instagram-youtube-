import logging

logger = logging.getLogger(__name__)


def _account_can_receive_jobs(account):
    """True when an enabled destination can actually be published to.

    Instagram/TikTok placeholders stay enabled so Meta sync can fill them, but
    they must not consume generation slots until a platform ID exists.
    """
    if not account or not account.get("enabled"):
        return False
    platform = str(account.get("platform", "") or "").strip().lower()
    if platform in ("instagram", "tiktok") and not str(
        account.get("platform_account_id") or ""
    ).strip():
        return False
    return True


def _hard_source_block(source):
    lab = source.get("lab", "").upper()
    if lab == "NON CERTIFIED":
        return False, "NON CERTIFIED - requires manual review"
    status = source.get("source_status", "").lower()
    if "error" in status or "429" in status or "api" in status:
        return False, "Source Status indicates API error - content may be incomplete"
    return True, ""


def _image_integrity_error(source):
    return str((source or {}).get("integrity_error", "") or "").strip()


def _video_url_matches_sku(source, url=""):
    from sheets_reader import SheetsReader

    sku = str((source or {}).get("sku") or "").strip()
    url = str(url or (source or {}).get("video_url") or "").strip()
    if not sku or not url:
        return False
    return SheetsReader._url_matches_sku(url, sku)


def _job_is_video_only(job):
    platform = str((job or {}).get("platform", "") or "").strip().lower()
    if platform not in ("youtube", "twitch"):
        return False
    selection = str((job or {}).get("media_selection", "") or "")
    return selection.startswith("model_video:") or selection == "product_video"


def _video_job_media_matches(job, source):
    selection = str((job or {}).get("media_selection", "") or "")
    if selection.startswith("model_video:"):
        try:
            idx = int(selection.split(":", 1)[1])
        except ValueError:
            idx = -1
        videos = list((source or {}).get("model_videos") or [])
        url = videos[idx] if 0 <= idx < len(videos) else ""
        return _video_url_matches_sku(source, url)
    return _video_url_matches_sku(source)


def _is_clean_source(source, job=None):
    clean, reason = _hard_source_block(source)
    if not clean:
        return False, reason
    integrity_error = _image_integrity_error(source)
    if not integrity_error:
        return True, ""
    if job and _job_is_video_only(job) and _video_job_media_matches(job, source):
        return True, ""
    return False, f"Source row integrity mismatch - {integrity_error}"


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


def is_model_media(job):
    """True for standalone model video/photo jobs."""
    selection = str((job or {}).get("media_selection", "") or "")
    return selection.startswith("model_video:") or selection.startswith("model_photo:")


def model_media_priority(job):
    """Always prefer model video, then model photo, over product media."""
    selection = str((job or {}).get("media_selection", "") or "")
    if selection.startswith("model_video:"):
        return 0
    if selection.startswith("model_photo:"):
        return 1
    if selection == "product_video":
        return 2
    return 3


def _append_model_jobs(jobs, sku, account_id, platform, account, source, photos=True, videos=True):
    if videos:
        for i in range(len(source.get("model_videos", []))):
            jobs.append(_make_job(sku, account_id, platform, "video", f"model_video:{i}", account))
    if photos:
        for i in range(len(source.get("model_images", []))):
            jobs.append(_make_job(sku, account_id, platform, "carousel", f"model_photo:{i}", account))


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

    Model video + model photo first, then product Reel/video, then product
    carousel, per the UPLOAD GUIDE format rules. Unclean rows (NON CERTIFIED,
    API error) are blocked from auto-publish and surfaced as a needs_review
    queue entry. Product-image SKU mismatches still allow YouTube/Twitch
    video jobs when the video URL itself belongs to the same SKU.
    """
    jobs = []
    for sku, source in sources.items():
        clean, reason = _hard_source_block(source)
        if not clean:
            logger.warning(f"SKU {sku}: blocked for auto-publish ({reason})")
            for account in accounts:
                if _account_can_receive_jobs(account):
                    jobs.append(_make_review_job(sku, account, reason))
                    break
            continue

        integrity_error = _image_integrity_error(source)
        if integrity_error:
            logger.warning(
                f"SKU {sku}: blocked for auto-publish (Source row integrity mismatch - {integrity_error})"
            )
            for account in accounts:
                if _account_can_receive_jobs(account):
                    jobs.append(_make_review_job(
                        sku, account,
                        f"Source row integrity mismatch - {integrity_error}",
                    ))
                    break

        has_carousel_media = bool(source["images"])

        for account in accounts:
            if not _account_can_receive_jobs(account):
                continue
            platform = account.get("platform", "")
            account_id = account.get("account_id", "")
            if integrity_error and platform not in ("youtube", "twitch"):
                continue

            if platform in ("facebook", "instagram", "line", "wechat", "pinterest", "x", "linkedin", "tiktok"):
                _append_model_jobs(jobs, sku, account_id, platform, account, source)
                if source["video_url"]:
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
                if has_carousel_media:
                    jobs.append(_make_job(sku, account_id, platform, "carousel", "carousel", account))
            elif platform in ("youtube", "twitch"):
                for i, url in enumerate(source.get("model_videos") or []):
                    if not integrity_error or _video_url_matches_sku(source, url):
                        jobs.append(_make_job(
                            sku, account_id, platform, "video",
                            f"model_video:{i}", account,
                        ))
                if source["video_url"] and (
                    not integrity_error or _video_url_matches_sku(source)
                ):
                    jobs.append(_make_job(sku, account_id, platform, "video", "product_video", account))
            elif platform in ("shopee", "lazada"):
                if has_carousel_media:
                    jobs.append(_make_job(sku, account_id, platform, "carousel", "carousel", account))

    logger.info(f"Generated {len(jobs)} job(s)")
    return jobs
