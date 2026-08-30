#!/usr/bin/env python3
"""Production runner for fair, safe social publishing.

Fixes queue starvation, account starvation, stale Meta auth failures, duplicate
publishing, and Instagram carousel ordering. Account selection rotates on every
10-minute production slot so every enabled page receives publishing turns.

Quota budget: production is tuned for up to 20 publish attempts/run. Maintenance
writes remain capped so the higher Google Sheets quota has comfortable headroom.
"""
import hashlib
import socket
from datetime import datetime, timedelta, timezone

import requests
import time
from urllib.parse import urlparse

import main
from config import Config
from job_generator import _is_clean_source

PRIMARY_PLATFORMS = ("instagram", "facebook", "youtube")
PREFLIGHT_SCAN_LIMIT = 1000
PER_ACCOUNT_SCAN_LIMIT = 300
HOUSEKEEPING_LIMIT = 8
REVIVE_LIMIT = 12
LOCK_PREFIX = "IDEMPOTENCY_LOCK"
FINGERPRINT_PREFIX = "MEDIA_FINGERPRINT"
_CURRENT_SHEETS = None
_DNS_CACHE = {}
_VIDEO_VALIDATION_CACHE = {}

# Every enabled primary account receives this rolling delivery floor. Five-hour
# spacing prevents all three posts being dumped together.
MINIMUM_POSTS_24H = 3
MINIMUM_GAP_HOURS = 5

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


def _fingerprint_marker(job, source):
    raw = repr(_media_fingerprint(job, source)).encode("utf-8")
    return f"{FINGERPRINT_PREFIX}:{hashlib.sha256(raw).hexdigest()}"


def _parse_queue_time(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _queue_state(sheets, now=None, accounts=None):
    """Return duplicate locks plus rolling upload activity in one queue read."""
    records = sheets.queue_ws.get_all_records(head=sheets.queue_header_row)
    reserved = set()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    protected = (
        {
            account_id for account_id, account in (accounts or {}).items()
            if account.get("enabled") and account.get("platform") in PRIMARY_PLATFORMS
        }
        if accounts is not None else set()
    )
    activity = {account_id: {"count": 0, "last": None} for account_id in protected}
    pattern = f"{FINGERPRINT_PREFIX}:"
    for rec in records:
        status = str(rec.get("status", "")).strip().lower()
        if status in (Config.JOB_STATUS_UPLOADED, "hold"):
            notes = str(rec.get("notes", "") or "")
            for part in notes.split("|"):
                part = part.strip()
                if part.startswith(pattern):
                    reserved.add(part.split()[0])

        account_id = str(rec.get("account_id", "") or "").strip()
        if status != Config.JOB_STATUS_UPLOADED or account_id not in activity:
            continue
        uploaded_at = _parse_queue_time(rec.get("last_attempt_at"))
        if not uploaded_at or uploaded_at < cutoff or uploaded_at > now:
            continue
        activity[account_id]["count"] += 1
        if activity[account_id]["last"] is None or uploaded_at > activity[account_id]["last"]:
            activity[account_id]["last"] = uploaded_at
    return reserved, activity


def _reserved_fingerprints(sheets):
    """Compatibility wrapper for callers needing duplicate locks only."""
    return _queue_state(sheets)[0]


def _minimum_delivery_priority(account_id, activity, now=None):
    """Prioritize lower delivery counts once the account's safety gap has elapsed."""
    now = now or datetime.now(timezone.utc)
    state = (activity or {}).get(account_id, {})
    count = int(state.get("count", 0) or 0)
    if count >= MINIMUM_POSTS_24H:
        return (1, count)
    last = state.get("last")
    if last and now - last < timedelta(hours=MINIMUM_GAP_HOURS):
        return (1, count)
    return (0, count)


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


def _video_validation_reason(url):
    """Return a stable corruption reason for a video, or an empty string.

    Content-Type checks cannot detect truncated MP4s or broken containers.  Probe
    each distinct video once per run and cache the result because the same product
    media is commonly queued for many destination accounts.  Network exceptions
    are treated as transient and left to the normal uploader retry path.
    """
    url = str(url or "").strip()
    if not url or main.media_kind(url) != "video":
        return ""
    if url in _VIDEO_VALIDATION_CACHE:
        return _VIDEO_VALIDATION_CACHE[url]
    try:
        reason = main.validate_media_url(url, kind="video", ffprobe=True) or ""
    except Exception as exc:
        main.logger.warning("Video preflight was inconclusive for %s: %s", url, exc)
        return ""
    _VIDEO_VALIDATION_CACHE[url] = reason
    return reason


def _media_preflight_reason(media):
    """Return why a job's media is definitively unusable, if known."""
    usable = 0
    for url in media:
        if not _dns_resolves(url):
            continue
        classification = main._classify_media_url(url)
        if classification == "invalid":
            continue
        usable += 1
        reason = _video_validation_reason(url)
        if reason:
            return f"video failed validation ({url}): {reason}"
    if not usable:
        return "all media URLs are unavailable, dead, DNS-invalid, or not media"
    return ""


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


def _healthy_candidates(
    jobs, accounts, sources, sheets, limit, reserved_fingerprints=None,
    recent_upload_activity=None,
):
    """Pick one healthy job per enabled account without cross-platform starvation.

    The old implementation globally sorted ~50k pending jobs and then inspected only
    the first 1,000. Because Instagram sorts ahead of Facebook/YouTube, that window
    could contain almost entirely one platform and produce just one selected job.
    This version groups the queue by account first, rotates enabled accounts fairly,
    and gives each account its own bounded preflight scan. A broken account/media
    backlog can no longer hide healthy work for every other account.
    """
    selected = []
    housekeeping = 0
    slots = _platform_limits(limit)
    seen_fingerprints = set()
    reserved_fingerprints = set(reserved_fingerprints or ())

    jobs_by_account = {}
    for job in jobs:
        account_id = str(job.get("account_id", "") or "").strip()
        if not account_id:
            continue
        jobs_by_account.setdefault(account_id, []).append(job)

    for account_jobs in jobs_by_account.values():
        account_jobs.sort(key=lambda j: (
            int(j.get("attempts", 0) or 0),
            int(j.get("row", 0) or 0),
        ))

    main.logger.info(
        "Account rotation slot: FB=%s IG=%s YT=%s",
        slots.get("facebook", 0), slots.get("instagram", 0), slots.get("youtube", 0),
    )

    for platform in ("facebook", "instagram", "youtube"):
        wanted = slots.get(platform, 0)
        if wanted <= 0:
            continue

        enabled_order = _enabled_account_order(accounts, platform)
        enabled_order.sort(key=lambda aid: (
            _minimum_delivery_priority(aid, recent_upload_activity),
            _rotation_rank(aid, platform, accounts, slots),
        ))
        platform_selected = 0

        for account_id in enabled_order:
            if len(selected) >= limit or platform_selected >= wanted:
                break

            account = accounts.get(account_id)
            if not account or not account.get("enabled"):
                continue

            account_jobs = jobs_by_account.get(account_id, [])
            if not account_jobs:
                continue

            chosen = None
            scanned = 0
            for job in account_jobs:
                if scanned >= PER_ACCOUNT_SCAN_LIMIT:
                    break
                scanned += 1

                if _is_locked(job):
                    continue
                if str(job.get("platform", "") or "").lower() != platform:
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

                clean_source, source_reason = _is_clean_source(source)
                if not clean_source:
                    if housekeeping < HOUSEKEEPING_LIMIT:
                        sheets.update_job(job, {
                            "status": Config.JOB_STATUS_NEEDS_REVIEW,
                            "notes": "Auto-blocked: source row integrity mismatch",
                            "error_message": source_reason,
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

                media_problem = _media_preflight_reason(media)
                if media_problem:
                    if housekeeping < HOUSEKEEPING_LIMIT:
                        sheets.update_job(job, {
                            "status": Config.JOB_STATUS_NEEDS_REVIEW,
                            "notes": "Auto-cleaned: media failed production preflight",
                            "error_message": f"Media preflight failed: {media_problem}",
                        })
                        housekeeping += 1
                    continue

                fingerprint = _media_fingerprint(job, source)
                marker = _fingerprint_marker(job, source)
                if marker in reserved_fingerprints:
                    if housekeeping < HOUSEKEEPING_LIMIT:
                        sheets.update_job(job, {
                            "status": Config.JOB_STATUS_SKIPPED,
                            "notes": "Duplicate media already uploaded or protected by idempotency lock",
                        })
                        housekeeping += 1
                    continue
                if fingerprint in seen_fingerprints:
                    continue

                chosen = job
                seen_fingerprints.add(fingerprint)
                reserved_fingerprints.add(marker)
                break

            if chosen:
                selected.append(chosen)
                platform_selected += 1
                main.logger.info(
                    "Selected account %s (%s), rotation rank=%s, account scan=%s",
                    account_id,
                    platform,
                    _rotation_rank(account_id, platform, accounts, slots),
                    scanned,
                )
            else:
                main.logger.warning(
                    "No healthy candidate for enabled account %s (%s) in first %s account jobs",
                    account_id, platform, min(len(account_jobs), PER_ACCOUNT_SCAN_LIMIT),
                )

    return selected


def _is_ambiguous_delivery_error(exc):
    """True when the platform may have accepted the post before the error."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    message = str(exc).lower()
    markers = (
        "timed out", "timeout", "connection reset", "remote disconnected",
        "broken pipe", "bad gateway", "service unavailable", "gateway timeout",
        "http 502", "http 503", "http 504",
    )
    return any(marker in message for marker in markers)


def guarded_publish(job, source, account):
    sheets = _CURRENT_SHEETS
    if sheets is None:
        return ORIGINAL_PUBLISH_JOB(job, source, account)

    old_notes = str(job.get("notes", "") or "")
    marker = _fingerprint_marker(job, source)
    lock_note = f"{LOCK_PREFIX}:{int(time.time())} | {marker}"
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
    except Exception as exc:
        if _is_ambiguous_delivery_error(exc):
            uncertain_note = f"{lock_note} | DELIVERY_UNCERTAIN:{str(exc)[:500]}"
            try:
                sheets.update_job(job, {"status": "hold", "notes": uncertain_note})
            except Exception as hold_error:
                main.logger.error(
                    "Job %s: could not persist uncertain delivery state: %s",
                    job.get("job_id"), hold_error,
                )
            raise main.DeliveryUncertainError(str(exc)) from exc
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
    # Production previously processed only existing queue rows. Accounts added
    # later could be enabled and verified forever without receiving any jobs.
    main.run_generate(sheets)
    main.logger.info("Fair queue generation complete; resetting Sheets quota window")
    time.sleep(65)
    main.read_upload_guide(sheets)
    accounts = {a["account_id"]: a for a in sheets.get_accounts()}
    sources = sheets.get_source_rows()

    _revive_stale_meta_failures(sheets, accounts)
    jobs = sheets.get_pending_jobs()
    if not jobs:
        main.logger.info("No pending jobs")
        return

    reserved_fingerprints, recent_upload_activity = _queue_state(sheets, accounts=accounts)
    main.logger.info("Loaded %s persistent media fingerprint(s)", len(reserved_fingerprints))
    for account_id, state in recent_upload_activity.items():
        main.logger.info(
            "DELIVERY_COVERAGE account=%s count_24h=%s target=%s",
            account_id, state.get("count", 0), MINIMUM_POSTS_24H,
        )
    selected = _healthy_candidates(
        jobs, accounts, sources, sheets, Config.MAX_JOBS_PER_RUN,
        reserved_fingerprints, recent_upload_activity,
    )
    main.logger.info(
        "Optimized queue: %s pending -> %s healthy job(s) selected",
        len(jobs), len(selected),
    )
    if not selected:
        main.logger.warning("No healthy upload candidates found in per-account preflight")
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
