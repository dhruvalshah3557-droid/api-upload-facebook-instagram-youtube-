#!/usr/bin/env python3
"""Production runner for fair, safe social publishing.

Fixes queue starvation, account starvation, stale Meta auth failures, duplicate
publishing, and Instagram carousel ordering. Account selection rotates on every
10-minute production slot so every enabled page receives publishing turns.

Quota budget: production is tuned for up to 20 publish attempts/run. Maintenance
writes remain capped so the higher Google Sheets quota has comfortable headroom.
"""
import socket
import time
from urllib.parse import urlparse

import main
from config import Config

PRIMARY_PLATFORMS = ("instagram", "facebook", "youtube")
PREFLIGHT_SCAN_LIMIT = 1000
HOUSEKEEPING_LIMIT = 8
REVIVE_LIMIT = 12
LOCK_PREFIX = "IDEMPOTENCY_LOCK"
_CURRENT_SHEETS = None
_DNS_CACHE = {}

_META_RETRY_MARKERS = (
    "unpublished posts must be posted to a page as the page itself",
    "no permission to publish the video",
    "error validating access token",
    "session is invalid",
    "session has been invalidated",
    "user logged out",
    "oauth",
)


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


def _is_locked(job):
    return LOCK_PREFIX in str(job.get("notes", "") or "")


def _media_fingerprint(job, source):
    media = resolve_media_fixed(job, source)
    return (
        str(job.get("account_id", "")),
        str(job.get("platform", "")),
        tuple(main._dedupe_media(media)),
    )


def _dns_resolves(url):
    """Return False for media hosts that do not exist in DNS.

    requests classifies DNS failures as generic connection errors. main.py keeps
    generic connection errors as 'unknown' so temporary network outages can retry,
    but a hostname with no DNS record is not transient. Reject it in preflight so
    one bad source URL cannot consume the account's publishing slot or fail the run.
    """
    try:
        host = (urlparse(str(url or "").strip()).hostname or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _DNS_CACHE:
        return _DNS_CACHE[host]
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ok = True
    except socket.gaierror:
        ok = False
    _DNS_CACHE[host] = ok
    if not ok:
        main.logger.warning("Media host does not resolve in DNS; rejecting URL: %s", url)
    return ok


def _enabled_account_order(accounts, platform):
    return [
        aid for aid, account in accounts.items()
        if account.get("enabled") and account.get("platform") == platform
    ]


def _platform_limits(limit):
    """Reserve most capacity for Meta while keeping YouTube continuously active."""
    if limit <= 1:
        return {"facebook": 1, "instagram": 0, "youtube": 0}
    if limit <= 3:
        return {"facebook": 1, "instagram": 1, "youtube": limit - 2}
    if limit >= 20:
        # Current production fleet: up to 16 Facebook pages, 8 enabled IG,
        # and one enabled YouTube channel. Eleven FB + all eight IG + one YT
        # gives broadest account coverage per 20-job cycle; FB rotates next run.
        return {"facebook": 11, "instagram": 8, "youtube": 1}
    fb = max(2, limit // 2)
    ig = max(1, limit - fb - 1)
    yt = max(0, limit - fb - ig)
    return {"facebook": fb, "instagram": ig, "youtube": yt}


def _rotation_rank(account_id, platform, accounts, slots):
    order = _enabled_account_order(accounts, platform)
    if not order or account_id not in order:
        return 999999
    per_run = max(1, slots.get(platform, 1))
    slot_number = int(time.time() // 600)
    start = (slot_number * per_run) % len(order)
    idx = order.index(account_id)
    return (idx - start) % len(order)


def _priority(job, accounts, slots):
    platform = job.get("platform", "")
    try:
        p = PRIMARY_PLATFORMS.index(platform)
    except ValueError:
        p = 9
    rank = _rotation_rank(job.get("account_id", ""), platform, accounts, slots)
    attempts = int(job.get("attempts", 0) or 0)
    return (p, rank, attempts, int(job.get("row", 0) or 0))


def _revive_stale_meta_failures(sheets, accounts):
    enabled = {
        aid for aid, a in accounts.items()
        if a.get("enabled") and a.get("platform") in ("facebook", "instagram")
    }
    if not enabled:
        return 0
    records = sheets.queue_ws.get_all_records(head=sheets.queue_header_row)
    revived = 0
    for idx, rec in enumerate(records, start=sheets.queue_header_row + 1):
        if revived >= REVIVE_LIMIT:
            break
        if str(rec.get("status", "")).strip().lower() != Config.JOB_STATUS_FAILED:
            continue
        account_id = str(rec.get("account_id", "")).strip()
        if account_id not in enabled:
            continue
        platform = str(rec.get("platform", "")).strip().lower()
        if platform not in ("facebook", "instagram"):
            continue
        error = str(rec.get("error_message", "") or "").lower()
        if not any(marker in error for marker in _META_RETRY_MARKERS):
            continue
        sheets.update_job({"row": idx}, {
            "status": "pending",
            "attempts": 0,
            "error_message": "",
            "notes": "Auto-revived after Meta credential/page-token repair",
        })
        revived += 1
    if revived:
        main.logger.info("Revived %s stale Meta auth/permission job(s)", revived)
    return revived


def _healthy_candidates(jobs, accounts, sources, sheets, limit):
    selected = []
    housekeeping = 0
    slots = _platform_limits(limit)
    per_platform = {p: 0 for p in PRIMARY_PLATFORMS}
    seen_fingerprints = set()
    seen_accounts = set()
    jobs = sorted(jobs, key=lambda j: _priority(j, accounts, slots))

    main.logger.info(
        "Account rotation slot: FB=%s IG=%s YT=%s",
        slots.get("facebook", 0), slots.get("instagram", 0), slots.get("youtube", 0),
    )

    for job in jobs[:PREFLIGHT_SCAN_LIMIT]:
        if len(selected) >= limit:
            break
        if _is_locked(job):
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

        platform = job.get("platform", "")
        if platform not in PRIMARY_PLATFORMS:
            continue
        if per_platform.get(platform, 0) >= slots.get(platform, 0):
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

        account_id = job.get("account_id", "")
        if account_id in seen_accounts:
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

        usable = [
            url for url in media
            if _dns_resolves(url) and main._classify_media_url(url) != "invalid"
        ]
        if not usable:
            if housekeeping < HOUSEKEEPING_LIMIT:
                sheets.update_job(job, {
                    "status": Config.JOB_STATUS_NEEDS_REVIEW,
                    "notes": "Auto-cleaned: all media URLs are unavailable/dead or DNS-invalid",
                    "error_message": "All resolved media URLs are unavailable (404/dead/DNS-invalid); nothing to publish",
                })
                housekeeping += 1
            continue

        fingerprint = _media_fingerprint(job, source)
        if fingerprint in seen_fingerprints:
            continue

        selected.append(job)
        seen_accounts.add(account_id)
        seen_fingerprints.add(fingerprint)
        per_platform[platform] = per_platform.get(platform, 0) + 1
        main.logger.info(
            "Selected account %s (%s), rotation rank=%s",
            account_id, platform, _rotation_rank(account_id, platform, accounts, slots),
        )

    return selected


def guarded_publish(job, source, account):
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
            sheets.update_job(job, {"status": "pending", "notes": old_notes})
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

    _revive_stale_meta_failures(sheets, accounts)
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
