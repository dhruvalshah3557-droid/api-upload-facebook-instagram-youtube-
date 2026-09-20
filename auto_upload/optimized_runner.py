#!/usr/bin/env python3
"""Production runner for fair, safe social publishing.

Fixes queue starvation, account starvation, stale Meta auth failures, duplicate
publishing, and Instagram carousel ordering. Account selection rotates on every
10-minute production slot so every enabled page receives publishing turns.

Quota budget: production is tuned for up to 50 publish attempts/run so every
publish-ready Facebook, Instagram and YouTube account can receive a turn.
Instagram and Facebook both get one slot per due account; Instagram keeps a
configurable per-run cap so the shared Meta app quota is not burst. Unused
Facebook budget is given to Instagram before YouTube. LINE is excluded while
its monthly Messaging API quota is exhausted.
Maintenance writes remain capped so Google Sheets quota has comfortable headroom.
"""
import hashlib
import os
import socket
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import time
from urllib.parse import urlparse

import main
from config import Config
from delivery_policy import (
    LINE_QUOTA_EXHAUSTED,
    MINIMUM_POSTS_24H,
    PRIMARY_PLATFORMS,
    due_deficit_accounts,
    minimum_delivery_priority,
    parse_queue_time,
    ready_platform_counts,
    rolling_activity,
    slot_eligible,
)
from job_generator import _is_clean_source, model_media_priority
from sheets_reader import SheetsReader
PREFLIGHT_SCAN_LIMIT = 1000
PER_ACCOUNT_SCAN_LIMIT = 300
HOUSEKEEPING_LIMIT = 24
REVIVE_LIMIT = 12
LOCK_PREFIX = "IDEMPOTENCY_LOCK"
FINGERPRINT_PREFIX = "MEDIA_FINGERPRINT"
JOB_ID_PREFIX = "STABLE_JOB_ID"
PRODUCT_ACCOUNT_PREFIX = "PRODUCT_ACCOUNT"
_CURRENT_SHEETS = None
_DNS_CACHE = {}
_VIDEO_VALIDATION_CACHE = {}

LOCAL_POSTING_SLOTS = ((2, 0), (8, 0), (12, 0), (16, 0), (20, 0))
SLOT_WINDOW_MINUTES = 45

INSTAGRAM_RATE_LIMIT_MARKER = "meta_rate_limit"
_UNUSABLE_PENDING_MARKERS = (
    "invalid_grant",
    "invalid_client",
    "401 client error",
    "unauthorized for url",
    "token has been expired or revoked",
)
_RETRIABLE_PREFLIGHT_MARKERS = (
    "media preflight failed",
    "all media urls are unavailable",
    "source row integrity mismatch",
    "dns-invalid",
    "not media",
    "auto-blocked: source row integrity mismatch",
    "auto-cleaned: media failed production preflight",
)
_DEAD_MEDIA_HOSTS = {
    "images.colourdiam.com",
    "videos.colourdiam.com",
    "cdn.colourdiam.com",
}
_LIVE_MEDIA_HOSTS = (
    "www.colourdiam.com",
    "colourdiam.com",
)
_REWRITE_LOGGED = set()


def _known_unusable_pending(job):
    """True when a pending row already recorded a permanent auth failure."""
    blob = " ".join((
        str(job.get("error_message", "") or ""),
        str(job.get("notes", "") or ""),
    )).lower()
    if any(marker in blob for marker in _RETRIABLE_PREFLIGHT_MARKERS):
        return False
    return any(marker in blob for marker in _UNUSABLE_PENDING_MARKERS)


def _rewrite_media_url(url):
    """Map dead Colour Diam CDN hosts onto the live product origin."""
    raw = str(url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    host = (parsed.hostname or "").strip().lower()
    if host not in _DEAD_MEDIA_HOSTS:
        return raw
    rewritten = parsed._replace(netloc=_LIVE_MEDIA_HOSTS[0]).geturl()
    if host not in _REWRITE_LOGGED:
        _REWRITE_LOGGED.add(host)
        main.logger.info("Rewrote dead media host %s onto %s", host, _LIVE_MEDIA_HOSTS[0])
    return rewritten


def _rewrite_media_urls(urls, include_fallbacks=False):
    rewritten = []
    seen = set()
    for url in urls or []:
        value = _rewrite_media_url(url)
        if value and value not in seen:
            seen.add(value)
            rewritten.append(value)
        if include_fallbacks and value and value != url:
            fallback = urlparse(value)._replace(netloc=_LIVE_MEDIA_HOSTS[1]).geturl()
            if fallback not in seen:
                seen.add(fallback)
                rewritten.append(fallback)
    return rewritten


def _instagram_rate_limit_active(jobs, now=None):
    """Persist Meta app cooldown across separate GitHub Actions processes."""
    now = now or datetime.now(timezone.utc)
    cooldown = max(60, int(os.getenv("IG_RATE_LIMIT_COOLDOWN_SECONDS", "900")))
    cutoff = now - timedelta(seconds=cooldown)
    for job in jobs:
        if str(job.get("platform", "") or "").lower() != "instagram":
            continue
        blob = " ".join((
            str(job.get("error_message", "") or ""),
            str(job.get("notes", "") or ""),
        )).lower()
        if (
            INSTAGRAM_RATE_LIMIT_MARKER not in blob
            and "application limit" not in blob
            and "application request limit" not in blob
        ):
            continue
        attempted_at = _parse_queue_time(job.get("last_attempt_at"))
        if attempted_at and attempted_at >= cutoff:
            return True, int((attempted_at + timedelta(seconds=cooldown) - now).total_seconds())
    return False, 0


_META_RETRY_MARKERS = (
    "unpublished posts must be posted to a page as the page itself",
    "no permission to publish the video",
    "error validating access token",
    "session is invalid",
    "session has been invalidated",
    "user logged out",
    "oauth",
)
_CAPTION_RETRY_MARKERS = (
    "missing required",
    "regional caption",
    "refusing english fallback",
    "auto-cleaned: regional caption preflight failed",
)


def resolve_media_fixed(job, source):
    selection = job.get("media_selection", "")
    if selection == "carousel" and job.get("platform", "").lower() == "instagram":
        # Product/model videos have their own Reel jobs. Keep carousels image-only
        # so a broken video cannot block the product's image post or publish twice.
        media = []
        main_image = source.get("main_image", "")
        if main_image:
            media.append(main_image)
        certificate_media = source.get("certificate_media_url", "")
        if certificate_media and main._is_carousel_image_url(certificate_media):
            media.append(certificate_media)
        media.extend(list(source.get("side_images", [])))
        return _rewrite_media_urls(main._dedupe_media(media)[:10])
    return _rewrite_media_urls(ORIGINAL_RESOLVE_MEDIA(job, source))


def _model_media_priority(job):
    """Always prefer model videos/photos over product media."""
    return model_media_priority(job)


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


def _job_id_marker(job):
    job_id = str(job.get("job_id", "") or "").strip()
    if not job_id:
        return ""
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return f"{JOB_ID_PREFIX}:{digest}"


def _job_sku(job):
    """Normalize Sheets numeric SKUs such as 1135.0 to 1135."""
    return SheetsReader._normalize_sku(job.get("sku", "") if job else "")


def _product_account_marker(job):
    """Stable lock for one product per destination, regardless of post format."""
    sku = _job_sku(job).lower()
    account_id = str(job.get("account_id", "") or "").strip().lower()
    platform = str(job.get("platform", "") or "").strip().lower()
    if not sku or not account_id:
        return ""
    digest = hashlib.sha256(
        f"{account_id}|{platform}|{sku}".encode("utf-8")
    ).hexdigest()
    return f"{PRODUCT_ACCOUNT_PREFIX}:{digest}"


def _paired_market_key(account_id):
    """Return the shared market key for an FB/IG destination pair."""
    account_id = str(account_id or "").strip().upper()
    if account_id.startswith("FB-") or account_id.startswith("IG-"):
        return account_id.split("-", 1)[1]
    return ""


_parse_queue_time = parse_queue_time
_slot_eligible = slot_eligible
_ready_platform_counts = ready_platform_counts
_minimum_delivery_priority = minimum_delivery_priority
_due_deficit_accounts = due_deficit_accounts


def _queue_state(sheets, now=None, accounts=None):
    """Return duplicate locks plus rolling upload activity in one queue read."""
    records = sheets.queue_ws.get_all_records(head=sheets.queue_header_row)
    reserved = set()
    now = now or datetime.now(timezone.utc)
    activity = rolling_activity(records, accounts or {}, now)
    patterns = (
        f"{FINGERPRINT_PREFIX}:", f"{JOB_ID_PREFIX}:",
        f"{PRODUCT_ACCOUNT_PREFIX}:",
    )
    for rec in records:
        status = str(rec.get("status", "") or "").strip().lower()
        if status in (Config.JOB_STATUS_UPLOADED, "hold"):
            job_marker = _job_id_marker(rec)
            if job_marker:
                reserved.add(job_marker)
            product_marker = _product_account_marker(rec)
            if product_marker:
                reserved.add(product_marker)
            notes = str(rec.get("notes", "") or "")
            for part in notes.split("|"):
                part = part.strip()
                if part.startswith(patterns):
                    reserved.add(part.split()[0])
    return reserved, activity


def _reserved_fingerprints(sheets):
    """Compatibility wrapper for callers needing duplicate locks only."""
    return _queue_state(sheets)[0]


def _local_slot_due(account_id, account, activity, now=None):
    """Return True only during an unfilled publishing slot in account-local time."""
    now = now or datetime.now(timezone.utc)
    timezone_name = str((account or {}).get("timezone", "") or "").strip() or "UTC"
    try:
        local_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        main.logger.warning(
            "Invalid timezone for %s: %s; using UTC", account_id, timezone_name
        )
        local_tz = timezone.utc
    local_now = now.astimezone(local_tz)
    slot = None
    for hour, minute in LOCAL_POSTING_SLOTS:
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            slot = candidate
    if slot is None:
        return False
    if local_now - slot > timedelta(minutes=SLOT_WINDOW_MINUTES):
        return False
    slot_utc = slot.astimezone(timezone.utc)
    successes = (activity or {}).get(account_id, {}).get("success_times", [])
    return not any(slot_utc <= uploaded_at <= now for uploaded_at in successes)


def _dns_resolves(url):
    """Return False for media hosts that do not exist in DNS.

    requests classifies DNS failures as generic connection errors. main.py keeps
    generic connection errors as 'unknown' so temporary network outages can retry,
    but a hostname with no DNS record is not transient. Reject it in preflight so
    one bad source URL cannot consume the account's publishing slot or fail the run.
    Dead Colour Diam CDN hosts are rewritten onto the live origin first.
    """
    url = _rewrite_media_url(url)
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


def _video_validation_reason(url, force=False):
    """Return a stable corruption reason for a video, or an empty string.

    Content-Type checks cannot detect truncated MP4s or broken containers.  Probe
    each distinct video once per run and cache the result because the same product
    media is commonly queued for many destination accounts.  Network exceptions
    are treated as transient and left to the normal uploader retry path.
    """
    url = str(url or "").strip()
    if not url or (not force and main.media_kind(url) != "video"):
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


def _media_preflight_reason(media, force_video=False):
    """Return why a job's media is definitively unusable, if known."""
    usable = 0
    for url in _rewrite_media_urls(media, include_fallbacks=True):
        if not _dns_resolves(url):
            continue
        classification = main._classify_media_url(url)
        if classification == "invalid":
            continue
        usable += 1
        reason = _video_validation_reason(url, force=force_video)
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


def _account_publish_ready(account):
    """Return False for an active destination that cannot publish yet.

    Blank Instagram IDs remain enabled and are probed every production run. If
    Meta can resolve the linked professional account, use it immediately;
    otherwise skip it without consuming one of the limited Instagram slots.
    """
    if account.get("platform") == "tiktok":
        return bool(account.get("platform_account_id") and (
            os.getenv(account.get("credential_property_key", "")) or os.getenv("ZERNIO_API_KEY")))
    if account.get("platform") != "instagram" or account.get("platform_account_id"):
        return True

    token = Config.get_token(account.get("credential_property_key", ""))
    if not token:
        main.logger.warning(
            "Active Instagram account %s has no token/ID yet; leaving enabled and skipping this run",
            account.get("account_id", ""),
        )
        return False

    try:
        resolved = main.InstagramUploader._resolve_ig_user_id(
            "",
            token,
            account.get("account_name", "") or account.get("username_or_channel", ""),
        )
    except main.IGAccountNotLinkedError as exc:
        main.logger.warning(
            "Active Instagram account %s is not linked in Meta yet; leaving enabled and skipping this run: %s",
            account.get("account_id", ""),
            exc,
        )
        return False

    account["platform_account_id"] = resolved
    main.logger.info(
        "Resolved active Instagram account %s to platform ID %s",
        account.get("account_id", ""),
        resolved,
    )
    return True


def _instagram_run_cap():
    return max(0, int(os.getenv("IG_MAX_JOBS_PER_RUN", "20")))


def _youtube_run_cap(remaining):
    raw = os.getenv("YT_MAX_JOBS_PER_RUN")
    if raw is None or not str(raw).strip():
        return max(0, int(remaining))
    return max(0, int(raw))


def _used_slots(slots):
    return (
        int(slots.get("facebook", 0) or 0)
        + int(slots.get("instagram", 0) or 0)
        + int(slots.get("youtube", 0) or 0)
        + int(slots.get("tiktok", 0) or 0)
    )


def _fill_instagram_leftover(slots, remaining, ready, instagram_cap):
    """Give unused Facebook budget to due Instagram accounts first."""
    ready_instagram = int((ready or {}).get("instagram", 0) or 0)
    current = int(slots.get("instagram", 0) or 0)
    if ready_instagram <= 0:
        return slots
    leftover = max(0, int(remaining) - _used_slots(slots))
    slots["instagram"] = min(instagram_cap, ready_instagram, current + leftover)
    return slots


def _fill_youtube_leftover(slots, remaining, ready=None):
    """Give unused run budget to YouTube after Facebook/Instagram/TikTok shares."""
    ready_youtube = int((ready or {}).get("youtube", 0) or 0)
    current = int(slots.get("youtube", 0) or 0)
    if current <= 0 and ready_youtube <= 0:
        return slots
    leftover = max(0, int(remaining) - _used_slots(slots))
    cap = _youtube_run_cap(remaining)
    slots["youtube"] = min(cap, current + leftover)
    return slots


def _demand_platform_counts(accounts, activity=None):
    """Prefer 24h-deficit accounts so a finished platform cannot hog the run."""
    ready = _ready_platform_counts(accounts)
    if activity is None:
        return ready
    demand = {"facebook": 0, "instagram": 0, "youtube": 0, "tiktok": 0}
    for item in _due_deficit_accounts(accounts, activity):
        platform = str(item.get("platform", "") or "").strip().lower()
        if platform in demand:
            demand[platform] += 1
    if sum(demand.values()) <= 0:
        return ready
    return demand


def _assign_platform_slots(empty, remaining, alloc, instagram_cap, ready):
    total = sum(alloc.values())
    if total <= 0:
        slots = dict(empty)
        slots["facebook"] = remaining
        return slots
    if total <= remaining:
        slots = dict(empty)
        slots.update(alloc)
        slots["instagram"] = min(int(slots.get("instagram", 0) or 0), instagram_cap)
        _fill_instagram_leftover(slots, remaining, ready, instagram_cap)
        return _fill_youtube_leftover(slots, remaining, ready)
    slots = dict(empty)
    assigned = 0
    for platform in ("instagram", "facebook", "youtube", "tiktok"):
        if alloc.get(platform, 0) <= 0:
            continue
        share = max(1, remaining * alloc[platform] // total)
        slots[platform] = min(alloc[platform], share)
        assigned += slots[platform]
    overflow = [p for p in ("instagram", "facebook", "youtube", "tiktok") if slots[p] < alloc.get(p, 0)]
    while assigned > remaining:
        reduced = False
        for platform in ("facebook", "youtube", "tiktok", "instagram"):
            if assigned <= remaining:
                break
            if slots[platform] > 1:
                slots[platform] -= 1
                assigned -= 1
                reduced = True
        if not reduced:
            break
    idx = 0
    while assigned < remaining and overflow:
        platform = overflow[idx % len(overflow)]
        if slots[platform] < alloc.get(platform, 0):
            slots[platform] += 1
            assigned += 1
        idx += 1
        if idx > remaining * 4:
            break
    slots["instagram"] = min(slots["instagram"], instagram_cap)
    _fill_instagram_leftover(slots, remaining, ready, instagram_cap)
    return _fill_youtube_leftover(slots, remaining, ready)


def _platform_limits(limit, accounts=None, activity=None):
    """Allocate platform slots without bursting the shared Instagram app quota.

    LINE is excluded while LINE_QUOTA_EXHAUSTED is set so Facebook, Instagram
    and YouTube keep the full production budget. Facebook and Instagram both
    receive one slot per due account. Instagram is still globally capped per
    workflow run so the shared Meta app quota is not exhausted. Accounts that
    already met the 24-hour floor do not reserve slots; that budget goes to
    Instagram first, then leftover YouTube jobs.
    """
    line = 0
    if not LINE_QUOTA_EXHAUSTED and limit >= 5:
        line = 1
    remaining = max(0, int(limit) - line)
    empty = {
        "facebook": 0, "instagram": 0, "youtube": 0, "tiktok": 0, "line": line,
    }
    if remaining <= 0:
        return empty

    instagram_cap = _instagram_run_cap()
    ready = _ready_platform_counts(accounts) if accounts is not None else None
    if ready is not None:
        alloc = _demand_platform_counts(accounts, activity)
        return _assign_platform_slots(empty, remaining, alloc, instagram_cap, ready)

    if remaining <= 1:
        slots = dict(empty)
        slots["facebook"] = 1
        return slots
    if remaining <= 3:
        slots = dict(empty)
        slots["facebook"] = 1
        slots["instagram"] = 1
        slots["youtube"] = max(0, remaining - 2)
        return _fill_youtube_leftover(slots, remaining)
    instagram = min(max(0, remaining - 2), instagram_cap)
    youtube = remaining - instagram - 1
    facebook = remaining - youtube - instagram
    slots = dict(empty)
    slots["facebook"] = facebook
    slots["instagram"] = instagram
    slots["youtube"] = youtube
    return _fill_youtube_leftover(slots, remaining)


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


def _revive_caption_blockers(sheets, accounts):
    """Requeue jobs parked as needs_review after empty regional captions.

    Native caption fallbacks now cover every regional language, so Sweden and
    similar markets must not stay blocked on leftover Source Import blanks.
    """
    enabled = {
        aid for aid, a in accounts.items()
        if a.get("enabled")
    }
    if not enabled:
        return 0
    records = sheets.queue_ws.get_all_records(head=sheets.queue_header_row)
    revived = 0
    for idx, rec in enumerate(records, start=sheets.queue_header_row + 1):
        if revived >= REVIVE_LIMIT:
            break
        status = str(rec.get("status", "")).strip().lower()
        if status not in (Config.JOB_STATUS_NEEDS_REVIEW, Config.JOB_STATUS_FAILED):
            continue
        account_id = str(rec.get("account_id", "")).strip()
        if account_id not in enabled:
            continue
        blob = " ".join((
            str(rec.get("error_message", "") or ""),
            str(rec.get("notes", "") or ""),
        )).lower()
        if not any(marker in blob for marker in _CAPTION_RETRY_MARKERS):
            continue
        sheets.update_job({"row": idx}, {
            "status": "pending",
            "attempts": 0,
            "error_message": "",
            "notes": "Auto-revived after native regional caption fallback",
        })
        revived += 1
    if revived:
        main.logger.info("Revived %s regional-caption blocked job(s)", revived)
    return revived



def _account_scan_jobs(account_jobs, limit=PER_ACCOUNT_SCAN_LIMIT):
    """Return a bounded scan that covers both fresh and deep backlog jobs."""
    jobs = list(account_jobs or ())
    limit = max(1, int(limit))
    if len(jobs) <= limit:
        return jobs

    fresh_count = min(len(jobs), max(1, limit // 3))
    selected = list(jobs[:fresh_count])
    remaining = limit - len(selected)
    tail = jobs[fresh_count:]
    if remaining <= 0 or not tail:
        return selected
    if remaining == 1:
        selected.append(tail[-1])
        return selected

    last = len(tail) - 1
    indexes = [round(i * last / (remaining - 1)) for i in range(remaining)]
    selected.extend(tail[index] for index in indexes)
    return selected

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
    instagram_cooldown, instagram_wait = _instagram_rate_limit_active(jobs)
    slots = _platform_limits(limit, accounts, recent_upload_activity)
    if instagram_cooldown:
        slots["instagram"] = 0
        remaining = max(0, int(limit) - int(slots.get("line", 0) or 0))
        ready = _ready_platform_counts(accounts) if accounts is not None else None
        slots = _fill_youtube_leftover(slots, remaining, ready)
    seen_fingerprints = set()
    reserved_fingerprints = set(reserved_fingerprints or ())
    paired_sku_by_market = {}

    jobs_by_account = {}
    for job in jobs:
        account_id = str(job.get("account_id", "") or "").strip()
        if not account_id:
            continue
        jobs_by_account.setdefault(account_id, []).append(job)

    for account_jobs in jobs_by_account.values():
        # Model videos/photos always outrank product media. Fresh zero-attempt
        # work still wins inside the same media class so a large historical
        # backlog cannot hide current healthy model posts.
        account_jobs.sort(key=lambda j: (
            _model_media_priority(j),
            int(j.get("attempts", 0) or 0),
            # An Instagram product carousel needs one container request per
            # image plus a parent-container and publish request. Prefer a
            # Reel/single media job when both are available so one account
            # turn does not exhaust the shared Meta application budget.
            # Model photos stay ahead of that Instagram carousel penalty.
            1 if (
                str(j.get("platform", "")).lower() == "instagram"
                and str(j.get("format", "")).lower() == "carousel"
                and not str(j.get("media_selection", "") or "").startswith("model_")
            ) else 0,
            -int(j.get("row", 0) or 0),
        ))

    main.logger.info(
        "Account rotation slot: FB=%s IG=%s YT=%s TT=%s LINE=%s",
        slots.get("facebook", 0), slots.get("instagram", 0),
        slots.get("youtube", 0), slots.get("tiktok", 0), slots.get("line", 0),
    )

    for platform in ("facebook", "instagram", "youtube", "tiktok", "line"):
        if platform == "instagram" and instagram_cooldown:
            main.logger.warning(
                "Instagram selection deferred for %ss: Meta application cooldown active",
                max(1, instagram_wait),
            )
            continue
        if platform == "line" and LINE_QUOTA_EXHAUSTED:
            continue
        wanted = slots.get(platform, 0)
        if wanted <= 0:
            continue

        enabled_order = _enabled_account_order(accounts, platform)
        # A local posting slot is preferred, but it must not block an account
        # that is below its rolling delivery floor. The old hard slot filter
        # left zero-delivery markets (for example Vietnam) pending indefinitely
        # whenever a workflow ran outside the narrow 45-minute slot window.
        enabled_order = [
            aid for aid in enabled_order
            if (
                _local_slot_due(aid, accounts.get(aid), recent_upload_activity)
                or _minimum_delivery_priority(
                    aid, recent_upload_activity
                )[0] == 0
            )
        ]
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
            if not _account_publish_ready(account):
                continue

            account_jobs = [
                job for job in jobs_by_account.get(account_id, [])
                if not _known_unusable_pending(job)
            ]
            if not account_jobs:
                continue

            scan_jobs = _account_scan_jobs(account_jobs)
            # Facebook is processed before Instagram. When both accounts share
            # the same regional suffix (for example FB-MMR and IG-MMR), prefer
            # the Facebook SKU for Instagram so the linked profiles publish
            # the same product instead of two independent rotations. If that
            # SKU has no healthy Instagram media, the normal fallback remains.
            if platform == "instagram":
                paired_sku = paired_sku_by_market.get(
                    _paired_market_key(account_id)
                )
                if paired_sku:
                    scan_jobs.sort(
                        key=lambda job: 0
                        if _job_sku(job) == paired_sku
                        else 1
                    )
            # YouTube is not Meta-quota bound. Consume leftover run budget with
            # multiple healthy jobs from the same channel instead of leaving
            # slots idle after one video.
            jobs_for_account = 1
            if platform == "youtube":
                jobs_for_account = max(1, wanted - platform_selected)
            account_selected = 0
            while (
                account_selected < jobs_for_account
                and platform_selected < wanted
                and len(selected) < limit
            ):
                chosen = None
                for job in scan_jobs:
                    if _is_locked(job):
                        continue
                    if str(job.get("platform", "") or "").lower() != platform:
                        continue
                    if _known_unusable_pending(job):
                        continue

                    job_marker = _job_id_marker(job)
                    if job_marker and job_marker in reserved_fingerprints:
                        if housekeeping < HOUSEKEEPING_LIMIT:
                            sheets.update_job(job, {
                                "status": Config.JOB_STATUS_SKIPPED,
                                "notes": "Duplicate stable job ID already uploaded or protected by idempotency lock",
                            })
                            housekeeping += 1
                        continue

                    product_marker = _product_account_marker(job)
                    if product_marker and product_marker in reserved_fingerprints:
                        if housekeeping < HOUSEKEEPING_LIMIT:
                            sheets.update_job(job, {
                                "status": Config.JOB_STATUS_SKIPPED,
                                "notes": "Duplicate product already uploaded to this account in another media format",
                            })
                            housekeeping += 1
                        continue

                    source = sources.get(_job_sku(job))
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

                    selection = str(job.get("media_selection", "") or "")
                    force_video = (
                        str(job.get("format", "") or "").strip().lower() == "video"
                        or selection == "product_video"
                        or selection.startswith("model_video:")
                    )
                    media_problem = _media_preflight_reason(
                        media, force_video=force_video
                    )
                    if media_problem:
                        if housekeeping < HOUSEKEEPING_LIMIT:
                            sheets.update_job(job, {
                                "status": Config.JOB_STATUS_NEEDS_REVIEW,
                                "notes": "Auto-cleaned: media failed production preflight",
                                "error_message": f"Media preflight failed: {media_problem}",
                            })
                            housekeeping += 1
                        continue

                    try:
                        main.build_caption(job, source, account)
                    except ValueError as exc:
                        caption_error = str(exc)
                        if housekeeping < HOUSEKEEPING_LIMIT:
                            sheets.update_job(job, {
                                "status": Config.JOB_STATUS_NEEDS_REVIEW,
                                "notes": "Auto-cleaned: regional caption preflight failed",
                                "error_message": caption_error,
                            })
                            housekeeping += 1
                        else:
                            main.logger.warning(
                                "Skipping %s for %s: %s",
                                job.get("job_id", ""),
                                account_id,
                                caption_error,
                            )
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
                    if job_marker:
                        reserved_fingerprints.add(job_marker)
                    if product_marker:
                        reserved_fingerprints.add(product_marker)
                    break

                if not chosen:
                    if account_selected == 0:
                        main.logger.warning(
                            "No healthy candidate for enabled account %s (%s) in bounded sample of %s/%s account jobs",
                            account_id, platform, len(scan_jobs), len(account_jobs),
                        )
                    break

                selected.append(chosen)
                platform_selected += 1
                account_selected += 1
                if platform == "facebook":
                    market_key = _paired_market_key(account_id)
                    if market_key:
                        paired_sku_by_market[market_key] = _job_sku(chosen)
                main.logger.info(
                    "Selected account %s (%s), rotation rank=%s, account scan=%s",
                    account_id,
                    platform,
                    _rotation_rank(account_id, platform, accounts, slots),
                    len(scan_jobs),
                )

    return selected


def _is_ambiguous_delivery_error(exc):
    """True when the platform may have accepted the post before the error."""
    if isinstance(exc, (main.DeliveryUncertainError, requests.Timeout, requests.ConnectionError)):
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
    job_marker = _job_id_marker(job)
    product_marker = _product_account_marker(job)
    lock_note = f"{LOCK_PREFIX}:{int(time.time())} | {marker}"
    if job_marker:
        lock_note = f"{lock_note} | {job_marker}"
    if product_marker:
        lock_note = f"{lock_note} | {product_marker}"
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
    _revive_caption_blockers(sheets, accounts)
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
    due = _due_deficit_accounts(accounts, recent_upload_activity)
    if due:
        main.logger.info(
            "DELIVERY_DEFICIT due=%s",
            ", ".join("%s:%s" % (item["account_id"], item["deficit"]) for item in due),
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
