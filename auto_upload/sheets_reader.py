import datetime
import os

import gspread
from config import Config
from oauth2client.service_account import ServiceAccountCredentials


class SheetsReader:
    SCOPE = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    # Header row (1-based) per worksheet. Source Import keeps its header in
    # row 1; Accounts / Publishing Queue / Publishing Log leave row 1 blank
    # and put their headers in row 2.
    SOURCE_HEADER_ROW = 1
    ACCOUNTS_HEADER_ROW = 2
    QUEUE_HEADER_ROW = 2
    LOG_HEADER_ROW = 2

    IMAGE_COLS = ["image1 link", "image2 link", "image3 link", "image4 link",
                  "image5 link", "image6 link", "image7 link", "image8 link"]

    LANG_CAPTION_COLS = {
        "my": "Burmese Description",
        "th": "Thai Description",
        "tl": "Filipino Description",
        "fil": "Filipino Description",
        "zh": "Chinese Description",
        "ru": "Russian Description",
        "ja": "Japanese Description",
        "ko": "Korean Description",
        "he": "israli description",
        "es": "spanish description",
        "ar": "arabic description",
    }
    LANG_TAG_COLS = {
        "my": "Burmese Hashtag",
        "th": "Thai Hashtag",
        "tl": "Filipino Hashtag",
        "fil": "Filipino Hashtag",
        "zh": "Chinese Hashtag",
        "ru": "Russian Hashtag",
        "ja": "Japanese Hashtag",
        "ko": "Korean Hashtag",
        "he": "israli hashtag",
        "es": "spanish hashtag",
        "ar": "arabic hashtag",
    }

    # Publishing Queue columns
    QUEUE_COLS = [
        "job_id", "sku", "account_id", "media_selection", "platform", "format",
        "language", "scheduled_at", "timezone", "stock_id_tag", "status",
        "attempts", "last_attempt_at", "platform_post_id", "published_url",
        "error_message", "notes", "tagging_status", "tag_stock_id_used",
        "caption_final",
    ]
    # Publishing Log columns
    LOG_COLS = [
        "job_id", "attempt_time", "result", "platform_post_id", "published_url",
        "api_error_code", "error_message", "next_retry_at",
        "raw_response_reference", "notes",
    ]

    SKIP_STATUSES = {
        Config.JOB_STATUS_UPLOADED,
        Config.JOB_STATUS_FAILED,
        Config.JOB_STATUS_SKIPPED,
        Config.JOB_STATUS_NEEDS_REVIEW,
    }

    def __init__(self):
        self.client = self._authenticate()
        if Config.GOOGLE_SHEET_URL:
            sheet = self.client.open_by_url(Config.GOOGLE_SHEET_URL)
        else:
            sheet = self.client.open(Config.GOOGLE_SHEET_NAME)
        self.source_ws = sheet.worksheet(Config.SOURCE_IMPORT_SHEET)
        self.accounts_ws = sheet.worksheet(Config.ACCOUNTS_SHEET)
        self.queue_ws = sheet.worksheet(Config.QUEUE_SHEET)
        self.log_ws = sheet.worksheet(Config.LOG_SHEET)
        self.source_cols = self._col_map(self.source_ws, self.SOURCE_HEADER_ROW)
        self.accounts_cols = self._col_map(self.accounts_ws, self.ACCOUNTS_HEADER_ROW)
        self.queue_cols = self._col_map(self.queue_ws, self.QUEUE_HEADER_ROW)
        self.log_cols = self._col_map(self.log_ws, self.LOG_HEADER_ROW)

    def _authenticate(self):
        creds_path = Config.GOOGLE_SHEET_CREDENTIALS
        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"Service account credentials not found at: {creds_path}"
            )
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, self.SCOPE)
        return gspread.authorize(creds)

    @staticmethod
    def _col_map(worksheet, header_row=1):
        headers = worksheet.row_values(header_row)
        return {str(h).strip().lower(): idx + 1 for idx, h in enumerate(headers)}

    @staticmethod
    def _col_letter(col_idx):
        letter = ""
        while col_idx > 0:
            col_idx -= 1
            letter = chr(ord("A") + col_idx % 26) + letter
            col_idx //= 26
        return letter

    @staticmethod
    def _pick(row, *names):
        for name in names:
            v = row.get(name)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    @staticmethod
    def _split_lines(value):
        if not value:
            return []
        items = []
        for line in str(value).replace("\r", "").split("\n"):
            line = line.strip()
            if line:
                items.append(line)
        return items

    @staticmethod
    def _normalize_sku(value):
        if value is None:
            return ""
        s = str(value).strip()
        if s.endswith(".0") and s[:-2].isdigit():
            return s[:-2]
        return s

    @staticmethod
    def _is_center(url):
        return "center" in os.path.basename(str(url).split("?")[0]).lower()

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------
    def get_accounts(self):
        records = self.accounts_ws.get_all_records(head=self.ACCOUNTS_HEADER_ROW)
        accounts = []
        for idx, rec in enumerate(records, start=self.ACCOUNTS_HEADER_ROW + 1):
            account_id = str(rec.get("account_id", "")).strip()
            if not account_id:
                continue
            enabled = str(rec.get("enabled", "")).strip().lower() in ("yes", "true", "1", "y")
            accounts.append({
                "row": idx,
                "account_id": account_id,
                "platform": str(rec.get("platform", "")).strip().lower(),
                "account_name": str(rec.get("account_name", "")).strip(),
                "platform_account_id": str(rec.get("platform_account_id", "")).strip(),
                "username_or_channel": str(rec.get("username_or_channel", "")).strip(),
                "primary_language": str(rec.get("primary_language", "")).strip(),
                "fallback_language": str(rec.get("fallback_language", "")).strip(),
                "timezone": str(rec.get("timezone", "")).strip(),
                "enabled": enabled,
                "allowed_formats": str(rec.get("allowed_formats", "")).strip(),
                "caption_style": str(rec.get("caption_style", "")).strip(),
                "hashtag_set": str(rec.get("hashtag_set", "")).strip(),
                "min_gap_minutes": str(rec.get("min_gap_minutes", "")).strip(),
                "product_tagging": str(rec.get("product_tagging", "")).strip().lower() in ("yes", "true", "1", "y"),
                "catalog_or_store_id": str(rec.get("catalog_or_store_id", "")).strip(),
                "credential_property_key": str(rec.get("credential_property_key", "")).strip(),
                "approval_required": str(rec.get("approval_required", "")).strip().lower() in ("yes", "true", "1", "y"),
                "notes": str(rec.get("notes", "")).strip(),
            })
        return accounts

    # ------------------------------------------------------------------
    # Source Import
    # ------------------------------------------------------------------
    def get_source_rows(self):
        records = self.source_ws.get_all_records(head=self.SOURCE_HEADER_ROW)
        sources = {}
        for idx, rec in enumerate(records, start=self.SOURCE_HEADER_ROW + 1):
            sku = self._normalize_sku(rec.get("STK", ""))
            if not sku:
                continue

            images = [v for v in (rec.get(c, "") for c in self.IMAGE_COLS) if str(v).strip()]
            images = [str(v).strip() for v in images]
            main_image = next((u for u in images if self._is_center(u)), "")
            side_images = self._split_lines(rec.get("multiple side image link", ""))
            if not side_images:
                side_images = [u for u in images if not self._is_center(u)]
            else:
                side_images = [u for u in side_images if not self._is_center(u)]

            model_images = []
            for c in ["model image link 1", "model image link 2", "model image link 3", "multiple model photo link"]:
                for u in self._split_lines(rec.get(c, "")):
                    if u not in model_images:
                        model_images.append(u)

            model_videos = []
            for c in ["model video link 1", "model video link 2", "model video link 3"]:
                for u in self._split_lines(rec.get(c, "")):
                    if u not in model_videos:
                        model_videos.append(u)
            for u in self._split_lines(rec.get("multiple model video link", "")):
                if u not in model_videos:
                    model_videos.append(u)

            lang_captions = {}
            for code, col in self.LANG_CAPTION_COLS.items():
                lang_captions[code] = self._pick(rec, col)
            lang_hashtags = {}
            for code, col in self.LANG_TAG_COLS.items():
                lang_hashtags[code] = self._pick(rec, col)

            sources[sku] = {
                "row": idx,
                "sku": sku,
                "sr_no": str(rec.get("SR NO", "")).strip(),
                "lab": str(rec.get("LAB", "")).strip(),
                "certificate_id": str(rec.get("CERTIFICATE ID.", "")).strip(),
                "source_status": str(rec.get("Status", "")).strip(),
                "product_link": str(rec.get("PRODUCT LINK", "")).strip(),
                "product_name": str(rec.get("PRODUCT NAME", "")).strip(),
                "images": images,
                "main_image": main_image,
                "side_images": side_images,
                "video_url": str(rec.get("video link", "")).strip(),
                "multiple_video": self._split_lines(rec.get("multiple video link", "")),
                "model_images": model_images,
                "model_videos": model_videos,
                "facebook_caption": self._pick(rec, "FACEBOOK CAPTION", "Facebook Caption"),
                "instagram_caption": self._pick(rec, "INSTAGRAM CAPTION", "Instagram Caption"),
                "youtube_shorts_caption": self._pick(rec, "YouTube Shorts Caption", "YOUTUBE SHORTS CAPTION"),
                "hashtags": self._pick(rec, "HASHTAGS", "Hashtags"),
                "lang_captions": lang_captions,
                "lang_hashtags": lang_hashtags,
            }
        return sources

    def get_source_row(self, sku):
        return self.get_source_rows().get(self._normalize_sku(sku))

    # ------------------------------------------------------------------
    # Publishing Queue
    # ------------------------------------------------------------------
    def get_pending_jobs(self):
        records = self.queue_ws.get_all_records(head=self.QUEUE_HEADER_ROW)
        jobs = []
        for idx, rec in enumerate(records, start=self.QUEUE_HEADER_ROW + 1):
            status = str(rec.get("status", "")).strip().lower()
            if status in self.SKIP_STATUSES:
                continue
            job_id = str(rec.get("job_id", "")).strip()
            if not job_id:
                continue
            jobs.append({
                "row": idx,
                "job_id": job_id,
                "sku": self._normalize_sku(rec.get("sku", "")),
                "account_id": str(rec.get("account_id", "")).strip(),
                "media_selection": str(rec.get("media_selection", "")).strip(),
                "platform": str(rec.get("platform", "")).strip().lower(),
                "format": str(rec.get("format", "")).strip().lower(),
                "language": str(rec.get("language", "")).strip(),
                "timezone": str(rec.get("timezone", "")).strip(),
                "stock_id_tag": self._normalize_sku(rec.get("stock_id_tag", "")),
                "tagging_status": str(rec.get("tagging_status", "")).strip() or "Pending",
                "tag_stock_id_used": self._normalize_sku(rec.get("tag_stock_id_used", "")),
                "caption_final": str(rec.get("caption_final", "")).strip(),
                "attempts": int(rec.get("attempts") or 0) if str(rec.get("attempts") or "").strip().isdigit() else 0,
                "notes": str(rec.get("notes", "")).strip(),
            })
        return jobs

    def find_job(self, sku, account_id, platform, fmt, media_selection):
        records = self.queue_ws.get_all_records(head=self.QUEUE_HEADER_ROW)
        for idx, rec in enumerate(records, start=self.QUEUE_HEADER_ROW + 1):
            if (self._normalize_sku(rec.get("sku", "")) == self._normalize_sku(sku)
                    and str(rec.get("account_id", "")).strip() == account_id
                    and str(rec.get("platform", "")).strip().lower() == platform
                    and str(rec.get("format", "")).strip().lower() == fmt
                    and str(rec.get("media_selection", "")).strip() == media_selection):
                return idx
        return None

    def get_existing_job_keys(self):
        """Set of (sku, account_id, platform, format, media_selection) already queued."""
        keys = set()
        records = self.queue_ws.get_all_records(head=self.QUEUE_HEADER_ROW)
        for rec in records:
            sku = self._normalize_sku(rec.get("sku", ""))
            if not sku:
                continue
            keys.add((sku, str(rec.get("account_id", "")).strip(),
                      str(rec.get("platform", "")).strip().lower(),
                      str(rec.get("format", "")).strip().lower(),
                      str(rec.get("media_selection", "")).strip()))
        return keys

    def append_jobs(self, jobs):
        if not jobs:
            return
        rows = []
        for job in jobs:
            rows.append([job.get(col, "") for col in self.QUEUE_COLS])
        self.queue_ws.append_rows(rows, value_input_option="USER_ENTERED")

    def update_job(self, job, updates):
        row = job.get("row") if isinstance(job, dict) else job
        if not row:
            return
        for key, value in updates.items():
            col_idx = self.queue_cols.get(str(key).strip().lower())
            if not col_idx:
                continue
            col = self._col_letter(col_idx)
            self.queue_ws.update(f"{col}{row}", [[value]])

    # ------------------------------------------------------------------
    # Publishing Log
    # ------------------------------------------------------------------
    def write_log(self, entry):
        row = [entry.get(col, "") for col in self.LOG_COLS]
        self.log_ws.append_row(row, value_input_option="USER_ENTERED")

    def log_entry(self, job, result, error_message="", api_error_code="", notes=""):
        now = datetime.datetime.utcnow().isoformat()
        return {
            "job_id": job.get("job_id", ""),
            "attempt_time": now,
            "result": result,
            "platform_post_id": job.get("platform_post_id", ""),
            "published_url": job.get("published_url", ""),
            "api_error_code": api_error_code,
            "error_message": error_message,
            "next_retry_at": "",
            "raw_response_reference": "",
            "notes": notes,
        }
