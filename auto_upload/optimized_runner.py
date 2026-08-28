#!/usr/bin/env python3
"""Production runner that drains healthy jobs before legacy broken queue items.

This leaves the proven uploader implementations untouched while fixing queue
starvation. It also enforces the requested Instagram carousel order:
video -> main image -> certificate image -> remaining product images.
"""
import time

import main
from config import Config

PRIMARY_PLATFORMS = ("instagram", "facebook", "youtube")
PREFLIGHT_SCAN_LIMIT = 180
HOUSEKEEPING_LIMIT = 12


def resolve_media_fixed(job, source):
    selection = job.get("media_selection", "")
    if selection == "carousel" and job.get("platform", "").lower() == "instagram":
        media = []
        product_video = source.get("video_url", "")
        if product_video and main._is_video_url(product_video):
            media.append(product_video)
        main_image = source.get("main_image", "")
        if main_image:
            media.append(main_image)
        certificate_media = source.get("certificate_media_url", "")
        if certificate_media and main._is_carousel_image_url(certificate_media):
            media.append(certificate_media)
        media.extend(list(source.get("side_images", [])))
        return main._dedupe_media(media)[:10]
    return ORIGINAL_RESOLVE_MEDIA(job, source)


def _priority(job):
    platform = job.get("platform", "")
    try:
        p = PRIMARY_PLATFORMS.index(platform)
    except ValueError:
        p = 9
    attempts = int(job.get("attempts", 0) or 0)
    # Healthy core platforms and never-attempted jobs first.
    return (p, attempts, int(job.get("row", 0) or 0))


def _healthy_candidates(jobs, accounts, sources, sheets, limit):
    selected = []
    housekeeping = 0
    per_platform = {p: 0 for p in PRIMARY_PLATFORMS}
    jobs = sorted(jobs, key=_priority)

    for job in jobs[:PREFLIGHT_SCAN_LIMIT]:
        if len(selected) >= limit:
            break
        account = accounts.get(job.get("account_id"))
        if not account or not account.get("enabled"):
            if housekeeping < HOUSEKEEPING_LIMIT:
                sheets.update_job(job, {
                    "status": Config.JOB_STATUS_SKIPPED,
                    "notes": "Auto-cleaned: account disabled or missing",
                })
                housekeeping += 1
            continue

        source = sources.get(job.get("sku"))
        if not source:
            if housekeeping < HOUSEKEEPING_LIMIT:
                sheets.update_job(job, {
                    "status": Config.JOB_STATUS_SKIPPED,
                    "notes": "Auto-cleaned: SKU missing from Source Import",
                })
                housekeeping += 1
            continue

        media = resolve_media_fixed(job, source)
        if not media:
            if housekeeping < HOUSEKEEPING_LIMIT:
                sheets.update_job(job, {
                    "status": Config.JOB_STATUS_NEEDS_REVIEW,
                    "notes": "Auto-cleaned: no media resolved",
                })
                housekeeping += 1
            continue

        usable = []
        for url in media:
            kind = main._classify_media_url(url)
            if kind != "invalid":
                usable.append(url)
        if not usable:
            if housekeeping < HOUSEKEEPING_LIMIT:
                sheets.update_job(job, {
                    "status": Config.JOB_STATUS_NEEDS_REVIEW,
                    "notes": "Auto-cleaned: all media URLs are unavailable/dead",
                    "error_message": "All resolved media URLs are unavailable (404/dead links); nothing to publish",
                })
                housekeeping += 1
            continue

        platform = job.get("platform", "")
        # Keep the batch balanced so one account/platform cannot starve others.
        if platform in per_platform and per_platform[platform] >= max(1, (limit + 2) // 3):
            continue
        selected.append(job)
        if platform in per_platform:
            per_platform[platform] += 1

    return selected


def process_optimized():
    sheets = main.open_sheets_with_retry()
    main.read_upload_guide(sheets)
    accounts = {a["account_id"]: a for a in sheets.get_accounts()}
    sources = sheets.get_source_rows()
    jobs = sheets.get_pending_jobs()
    if not jobs:
        main.logger.info("No pending jobs")
        return

    selected = _healthy_candidates(jobs, accounts, sources, sheets, Config.MAX_JOBS_PER_RUN)
    main.logger.info(
        "Optimized queue: %s pending -> %s healthy job(s) selected",
        len(jobs), len(selected),
    )
    if not selected:
        main.logger.warning("No healthy upload candidates found in preflight window")
        return

    # process_pending normally rereads the queue; temporarily return only the
    # preflight-approved jobs so upload slots cannot be consumed by legacy junk.
    original_get_pending = sheets.get_pending_jobs
    sheets.get_pending_jobs = lambda: selected
    try:
        main.process_pending(sheets)
    finally:
        sheets.get_pending_jobs = original_get_pending


ORIGINAL_RESOLVE_MEDIA = main.resolve_media
main.resolve_media = resolve_media_fixed

if __name__ == "__main__":
    main.logger.info("=== Optimized Production Upload ===")
    process_optimized()
