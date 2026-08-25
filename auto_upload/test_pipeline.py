#!/usr/bin/env python3
"""Mock-based pipeline tests (no real credentials required).

Verifies the two reliability guarantees:
  1. `python main.py --generate` is idempotent: running it twice appends no
     duplicate job rows (unique key = SKU + account_id + platform + format +
     media_type). Only missing jobs are added.
  2. One failing job never stops the other jobs in the same run, results are
     written back to the Publishing Queue, and a second run does not re-post
     already successful jobs.

Run: python3 test_pipeline.py
"""
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _install(name, attrs):
    """Register a fake (possibly dotted) module in sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    parent = sys.modules.get(".".join(name.split(".")[:-1]))
    if parent is not None:
        setattr(parent, name.split(".")[-1], mod)
    return mod


class _FakeAPIError(Exception):
    def __init__(self, status_code=429):
        self.response = types.SimpleNamespace(status_code=status_code)
        super().__init__("rate limited")


gspread = _install("gspread", {})
gspread.exceptions = types.SimpleNamespace(APIError=_FakeAPIError)

_install("oauth2client", {})
_install("oauth2client.service_account", {
    "ServiceAccountCredentials": lambda *a, **k: None,
})

_install("dotenv", {"load_dotenv": lambda *a, **k: None})
_install("requests", {"get": lambda *a, **k: None, "post": lambda *a, **k: None})

_install("google_auth_oauthlib", {})
_install("google_auth_oauthlib.flow", {"InstalledAppFlow": lambda *a, **k: None})
_install("google.auth", {})
_install("google.auth.transport", {})
_install("google.auth.transport.requests", {"Request": lambda *a, **k: None})
_install("google.oauth2", {})
_install("google.oauth2.credentials", {"Credentials": lambda *a, **k: None})
_install("googleapiclient", {})
_install("googleapiclient.discovery", {"build": lambda *a, **k: None})
_install("googleapiclient.http", {"MediaIoBaseUpload": lambda *a, **k: None})

from config import Config  # noqa: E402
from sheets_reader import SheetsReader  # noqa: E402
import main  # noqa: E402

QUEUE_COLS = SheetsReader.QUEUE_COLS
SKIP_STATUSES = {
    Config.JOB_STATUS_UPLOADED,
    Config.JOB_STATUS_FAILED,
    Config.JOB_STATUS_SKIPPED,
    Config.JOB_STATUS_NEEDS_REVIEW,
}


class _FakeLogWorksheet:
    """Minimal stand-in for the Publishing Log worksheet used by
    insert_logs_newest_first: converts inserted value rows back into
    LOG_COLS-keyed dicts appended to the owning FakeSheets.log_entries."""

    def __init__(self, fake_sheets):
        self._sheets = fake_sheets

    def insert_rows(self, rows, row=None, value_input_option=None):
        for values in rows:
            entry = {}
            for i, col in enumerate(FakeSheets.LOG_COLS):
                entry[col] = values[i] if i < len(values) else ""
            self._sheets.log_entries.append(entry)


class FakeSheets:
    """In-memory stand-in for SheetsReader used by main.run_generate/process_pending."""

    LOG_COLS = SheetsReader.LOG_COLS

    def __init__(self, source_rows, accounts, queue_rows):
        self.guide_error = ""
        self.source_rows = dict(source_rows)
        self.accounts = list(accounts)
        self.queue_rows = list(queue_rows)
        self.append_jobs_calls = 0
        self.appended_jobs = []
        self.updated = []
        self.log_entries = []
        self.log_header_row = 2
        self.log_ws = _FakeLogWorksheet(self)

    def get_upload_guide(self):
        return []

    def guide_safety_rules(self, guide_rows):
        return []

    def get_accounts(self):
        return list(self.accounts)

    def get_source_rows(self):
        return dict(self.source_rows)

    def get_pending_jobs(self):
        jobs = []
        for i, rec in enumerate(self.queue_rows, start=2):
            status = str(rec.get("status", "")).strip().lower()
            if status in SKIP_STATUSES or not str(rec.get("job_id", "")).strip():
                continue
            jobs.append({
                "row": i,
                "job_id": str(rec.get("job_id", "")).strip(),
                "sku": str(rec.get("sku", "")).strip(),
                "account_id": str(rec.get("account_id", "")).strip(),
                "media_selection": str(rec.get("media_selection", "")).strip(),
                "platform": str(rec.get("platform", "")).strip().lower(),
                "format": str(rec.get("format", "")).strip().lower(),
                "language": str(rec.get("language", "")).strip(),
                "timezone": str(rec.get("timezone", "")).strip(),
                "stock_id_tag": str(rec.get("stock_id_tag", "")).strip(),
                "tagging_status": str(rec.get("tagging_status", "")).strip() or "Pending",
                "tag_stock_id_used": str(rec.get("tag_stock_id_used", "")).strip(),
                "caption_final": str(rec.get("caption_final", "")).strip(),
                "attempts": int(rec.get("attempts") or 0),
                "notes": str(rec.get("notes", "")).strip(),
            })
        return jobs

    def get_existing_job_keys(self):
        keys = set()
        for rec in self.queue_rows:
            sku = str(rec.get("sku", "")).strip()
            if not sku:
                continue
            keys.add((sku, str(rec.get("account_id", "")).strip(),
                      str(rec.get("platform", "")).strip().lower(),
                      str(rec.get("format", "")).strip().lower(),
                      str(rec.get("media_selection", "")).strip()))
        return keys

    def append_jobs(self, jobs):
        self.append_jobs_calls += 1
        for job in jobs:
            self.appended_jobs.append(job)
            self.queue_rows.append({col: job.get(col, "") for col in QUEUE_COLS})

    def update_job(self, job, updates):
        self.updated.append((job, dict(updates)))
        rec = self.queue_rows[job["row"] - 2]
        for k, v in updates.items():
            rec[k] = v

    def log_entry(self, job, result, error_message="", api_error_code="", notes=""):
        return {
            "job_id": job.get("job_id", ""),
            "attempt_time": "2026-01-01T00:00:00",
            "result": result,
            "platform_post_id": job.get("platform_post_id", ""),
            "published_url": job.get("published_url", ""),
            "api_error_code": api_error_code,
            "error_message": error_message,
            "next_retry_at": "",
            "raw_response_reference": "",
            "notes": notes,
        }

    def append_logs(self, entries):
        self.log_entries.extend(entries)


def _account(account_id="FB-ISR", platform="facebook", enabled=True):
    return {
        "row": 2,
        "account_id": account_id,
        "platform": platform,
        "account_name": account_id,
        "platform_account_id": "123456789",
        "username_or_channel": "",
        "primary_language": "en",
        "fallback_language": "",
        "timezone": "UTC",
        "enabled": enabled,
        "allowed_formats": "",
        "caption_style": "",
        "hashtag_set": "",
        "min_gap_minutes": "",
        "product_tagging": False,
        "catalog_or_store_id": "",
        "credential_property_key": "META_TOKEN_FB_ISR",
        "approval_required": False,
        "notes": "",
    }


def _source(sku="100"):
    return {
        "row": 2,
        "sku": sku,
        "sr_no": "1",
        "lab": "",
        "certificate_id": "",
        "source_status": "",
        "product_link": "",
        "product_name": "Diamond Ring",
        "images": [f"http://example.com/{sku}_center.jpg", f"http://example.com/{sku}_side1.jpg"],
        "main_image": f"http://example.com/{sku}_center.jpg",
        "side_images": [f"http://example.com/{sku}_side1.jpg"],
        "video_url": f"http://example.com/{sku}.mp4",
        "multiple_video": [],
        "model_images": [],
        "model_videos": [f"http://example.com/{sku}_model1.mp4"],
        "facebook_caption": "Caption",
        "instagram_caption": "Caption",
        "youtube_shorts_caption": "Caption",
        "youtube_product_id": "",
        "hashtags": "#diamond",
        "lang_captions": {},
        "lang_hashtags": {},
    }


def test_generate_is_idempotent():
    accounts = [_account()]
    sources = {"100": _source("100")}
    fake = FakeSheets(sources, accounts, [])
    assert fake.get_existing_job_keys() == set()

    main.run_generate(fake)
    first_count = len(fake.appended_jobs)
    # 1 FB account x (carousel + product_video + model_video:0) = 3 jobs
    assert first_count == 3, f"expected 3 jobs, got {first_count}"

    # Second identical run must append NOTHING new.
    main.run_generate(fake)
    assert len(fake.appended_jobs) == first_count, "duplicate jobs generated on second run"

    # A brand-new SKU appears -> only its missing jobs are added.
    fake.source_rows["200"] = _source("200")
    main.run_generate(fake)
    assert len(fake.appended_jobs) == first_count + 3

    # No duplicate unique keys ever.
    keys = [main.job_unique_key(j) for j in fake.appended_jobs]
    assert len(keys) == len(set(keys)), "duplicate unique job keys produced"
    print("OK test_generate_is_idempotent")


def test_one_failure_does_not_stop_others():
    accounts = [_account("FB-A"), _account("FB-B")]
    sources = {"100": _source("100")}
    jobs = [
        {"job_id": "100-FB-A-carousel", "sku": "100", "account_id": "FB-A",
         "media_selection": "carousel", "platform": "facebook", "format": "carousel",
         "status": "pending", "attempts": 0},
        {"job_id": "100-FB-B-carousel", "sku": "100", "account_id": "FB-B",
         "media_selection": "carousel", "platform": "facebook", "format": "carousel",
         "status": "pending", "attempts": 0},
    ]
    fake = FakeSheets(sources, accounts, jobs)

    def fake_publish(job, source, account):
        if job["account_id"] == "FB-A":
            raise Exception("(#190) Invalid OAuth 2.0 Access Token")
        return "111111111", "https://www.facebook.com/123456789/posts/111111111"

    with patch.object(Config, "MAX_JOB_ATTEMPTS", 1), \
         patch.object(Config, "MAX_JOBS_PER_RUN", 40), \
         patch("main.publish_job", side_effect=fake_publish), \
         patch("main.time.sleep"):
        main.process_pending(fake)

    status_by_job = {job_id: updates["status"] for job_id, updates in
                     ((u[0]["job_id"], u[1]) for u in fake.updated)}
    assert status_by_job["100-FB-A-carousel"] == Config.JOB_STATUS_FAILED
    assert status_by_job["100-FB-B-carousel"] == Config.JOB_STATUS_UPLOADED

    fb_b_updates = next(u[1] for u in fake.updated if u[0]["job_id"] == "100-FB-B-carousel")
    assert fb_b_updates.get("platform_post_id") == "111111111"
    assert fb_b_updates.get("published_url").startswith("https://www.facebook.com/")

    assert sorted(e["result"] for e in fake.log_entries) == ["failed", "success"]
    print("OK test_one_failure_does_not_stop_others")


def test_second_run_does_not_repost_uploaded():
    accounts = [_account("FB-A")]
    sources = {"100": _source("100")}
    jobs = [
        {"job_id": "100-FB-A-carousel", "sku": "100", "account_id": "FB-A",
         "media_selection": "carousel", "platform": "facebook", "format": "carousel",
         "status": "pending", "attempts": 0},
    ]
    fake = FakeSheets(sources, accounts, jobs)
    calls = {"n": 0}

    def fake_publish(job, source, account):
        calls["n"] += 1
        return "111111111", "https://www.facebook.com/123456789/posts/111111111"

    with patch("main.publish_job", side_effect=fake_publish), patch("main.time.sleep"):
        main.process_pending(fake)
    assert calls["n"] == 1
    assert fake.queue_rows[0]["status"] == Config.JOB_STATUS_UPLOADED

    # Second run: the job is already "uploaded" -> not picked up, not re-posted.
    with patch("main.publish_job", side_effect=fake_publish), patch("main.time.sleep"):
        main.process_pending(fake)
    assert calls["n"] == 1, "successful job was re-posted on a second run"
    print("OK test_second_run_does_not_repost_uploaded")


def test_generate_new_platforms():
    accounts = [
        _account("LINE-CD", "line"),
        _account("WECHAT-CD", "wechat"),
        _account("PINTEREST-CD", "pinterest"),
    ]
    sources = {"100": _source("100")}
    fake = FakeSheets(sources, accounts, [])

    with patch.object(Config, "MAX_GENERATE_JOBS", 40):
        main.run_generate(fake)
    jobs = fake.appended_jobs
    # 3 platforms x (carousel + product_video + model_video:0) = 9 jobs
    assert len(jobs) == 9, f"expected 9 jobs, got {len(jobs)}"
    platforms = {j["platform"] for j in jobs}
    assert platforms == {"line", "wechat", "pinterest"}
    formats = {j["format"] for j in jobs}
    assert formats == {"carousel", "video"}
    assert any(j["format"] == "video" and j["media_selection"] == "product_video" for j in jobs)

    # Idempotent on second run.
    with patch.object(Config, "MAX_GENERATE_JOBS", 40):
        main.run_generate(fake)
    assert len(fake.appended_jobs) == 9, "duplicate jobs generated for new platforms"
    print("OK test_generate_new_platforms")


def test_publish_new_platforms_routing():
    accounts = [
        _account("LINE-CD", "line"),
        _account("WECHAT-CD", "wechat"),
        _account("PINTEREST-CD", "pinterest"),
    ]
    sources = {"100": _source("100")}
    jobs = [
        {"job_id": "100-LINE-CD-carousel", "sku": "100", "account_id": "LINE-CD",
         "media_selection": "carousel", "platform": "line", "format": "carousel",
         "status": "pending", "attempts": 0},
        {"job_id": "100-WECHAT-CD-carousel", "sku": "100", "account_id": "WECHAT-CD",
         "media_selection": "carousel", "platform": "wechat", "format": "carousel",
         "status": "pending", "attempts": 0},
        {"job_id": "100-PINTEREST-CD-product_video", "sku": "100", "account_id": "PINTEREST-CD",
         "media_selection": "product_video", "platform": "pinterest", "format": "video",
         "status": "pending", "attempts": 0},
    ]
    fake = FakeSheets(sources, accounts, jobs)

    class FakeLine:
        def __init__(self, *a, **k):
            self.called = False
        def upload_carousel(self, *a, **k):
            return {"id": "111", "url": "https://line.example/111"}

    class FakeWeChat:
        def __init__(self, *a, **k):
            self.called = False
        def upload_carousel(self, *a, **k):
            return {"id": "222", "url": "https://mp.weixin.qq.com/"}

    class FakePinterest:
        def __init__(self, *a, **k):
            self.called = False
        def upload(self, *a, **k):
            return {"id": "333", "url": "https://www.pinterest.com/pin/333/"}

    with patch.object(Config, "MAX_JOB_ATTEMPTS", 1), \
         patch.object(Config, "MAX_JOBS_PER_RUN", 40), \
         patch("main.time.sleep"), \
         patch("main.LineUploader", FakeLine), \
         patch("main.WeChatUploader", FakeWeChat), \
         patch("main.PinterestUploader", FakePinterest), \
         patch.dict(os.environ, {
             "LINE_CHANNEL_ACCESS_TOKEN": "line-token",
             "WECHAT_APPID": "wx-appid",
             "WECHAT_APPSECRET": "wx-secret",
             "PINTEREST_ACCESS_TOKEN": "pin-token",
         }):
        main.process_pending(fake)

    statuses = {u[0]["job_id"]: u[1]["status"] for u in fake.updated}
    for job_id in ("100-LINE-CD-carousel", "100-WECHAT-CD-carousel", "100-PINTEREST-CD-product_video"):
        assert statuses[job_id] == Config.JOB_STATUS_UPLOADED, job_id
    assert fake.updated[0][1]["published_url"].startswith("https://")
    print("OK test_publish_new_platforms_routing")


def test_round_robin_jobs_mix_platforms():
    jobs = [
        {"job_id": "a", "platform": "facebook"},
        {"job_id": "b", "platform": "facebook"},
        {"job_id": "c", "platform": "instagram"},
        {"job_id": "d", "platform": "youtube"},
        {"job_id": "e", "platform": "instagram"},
        {"job_id": "f", "platform": "facebook"},
    ]
    selected = main._round_robin_jobs(jobs, 5)
    picked = [j["job_id"] for j in selected]
    assert picked == ["a", "c", "d", "b", "e"], picked
    counts = {p: 0 for p in ("facebook", "instagram", "youtube")}
    for j in selected:
        counts[j["platform"]] += 1
    assert counts == {"facebook": 2, "instagram": 2, "youtube": 1}, counts
    assert main._round_robin_jobs([], 5) == []
    print("OK test_round_robin_jobs_mix_platforms")


if __name__ == "__main__":
    test_generate_is_idempotent()
    test_one_failure_does_not_stop_others()
    test_second_run_does_not_repost_uploaded()
    test_generate_new_platforms()
    test_publish_new_platforms_routing()
    test_round_robin_jobs_mix_platforms()
    print("All pipeline tests passed.")
