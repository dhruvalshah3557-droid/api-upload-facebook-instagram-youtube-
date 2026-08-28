#!/usr/bin/env python3
"""Production runner that drains healthy jobs before legacy broken queue items.

This leaves the proven uploader implementations untouched while fixing queue
starvation. It also enforces the requested Instagram carousel order:
video -> main image -> certificate image -> remaining product images.

Duplicate protection: immediately before any platform API call the queue row is
locked in Sheets. If the platform accepts the post but the runner dies before
Sheets records success, that lock remains and later runs will not publish the
same job again. If the API call itself fails normally, the lock is cleared so
main.py can apply the usual retry/needs_review policy.
"""
import time

import main
from config import Config

PRIMARY_PLATFORMS = ("instagram", "facebook", "youtube")
PREFLIGHT_SCAN_LIMIT = 180
HOUSEKEEPING_LIMIT = 12
LOCK_PREFIX = "IDEMPOTENCY_LOCK"
_CURRENT_SHEETS = None


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
    return (p, attempts, int(job.get("row", 0) or 0))


def _is_locked(job):
    return LOCK_PREFIX in str(job.get("notes", "") or "")


def _media_fingerprint(job, source):
    media = resolve_media_fixed(job, source)
    return (
        str(job.get("account_id", "")),
        str(job.get("platform", "")),
        tuple(main._dedupe_media(media)),
    )


def _healthy_candidates(jobs, accounts, sources, sheets, limit):
    selected = []
    housekeeping = 0
    per_platform = {p: 0 for p in PRIMARY_PLATFORMS}
    seen_fingerprints = set()
    jobs = sorted(jobs, key=_priority)

    for job in jobs[:PREFLIGHT_SCAN_LIMIT]:
        if len(selected) >= limit:
            break

        if _is_locked(job):
            main.logger.warning("Job %s skipped by idempotency lock", job.get("job_id"))
            continue

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

        fingerprint = _media_fingerprint(job, source)
        if fingerprint in seen_fingerprints:
            main.logger.warning(
                "Job %s skipped: duplicate account/media fingerprint in this batch",
                job.get("job_id"),
            )
            continue

        platform = job.get("platform", "")
        if platform in per_platform and per_platform[platform] >= max(1, (limit + 2) // 3):
            continue

        selected.append(job)
        seen_fingerprints.add(fingerprint)
        if platform in per_platform:
            per_platform[platform] += 1

    return selected


def guarded_publish(job, source, account):
    """Lock a job before the external publish call to guarantee at-most-once posting."""
    sheets = _CURRENT_SHEETS
    if sheets is None:
        return ORIGINAL_PUBLISH_JOB(job, source, account)

    old_notes = str(job.get("notes", "") or "")
    lock_note = f"{LOCK_PREFIX}:{int(time.time())}"
    if old_notes:
        lock_note = f"{lock_note} | {old_notes}"

    sheets.update_job(job, {
        "status": "hold",
        "notes": lock_note,
        "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    main.logger.info("Job %s: idempotency lock saved before publish", job.get("job_id"))

    try:
        return ORIGINAL_PUBLISH_JOB(job, source, account)
    except Exception:
        try:
            sheets.update_job(job, {
                "status": "pending",
                "notes": old_notes,
            })
            main.logger.info("Job %s: idempotency lock released after API failure", job.get("job_id"))
        except Exception as unlock_error:
            main.logger.error(
                "Job %s: could not release idempotency lock: %s",
                job.get("job_id"), unlock_error,
            )
        raise


def process_optimized():
    global _CURRENT_SHEETS
    sheets = main.open_sheets_with_retry()
    _CURRENT_SHEETS = sheets
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

    original_get_pending = sheets.get_pending_jobs
    sheets.get_pending_jobs = lambda: selected
    try:
        main.process_pending(sheets)
    finally:
        sheets.get_pending_jobs = original_get_pending
        _CURRENT_SHEETS = None


ORIGINAL_RESOLVE_MEDIA = main.resolve_media
ORIGINAL_PUBLISH_JOB = main.publish_job
main.resolve_media = resolve_media_fixed
main.publish_job = guarded_publish

if __name__ == "__main__":
    main.logger.info("=== Optimized Production Upload ===")
    process_optimized()
