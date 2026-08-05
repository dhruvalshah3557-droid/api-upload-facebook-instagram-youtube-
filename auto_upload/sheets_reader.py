import json
import os
import logging

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from config import Config

logger = logging.getLogger(__name__)

POSTED_STATUSES = {"uploaded", "posted", "done", "ok"}


class SheetsReader:
    SCOPE = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    STATUS_COL = "Status"
    NOTES_COL = "Notes"

    def __init__(self):
        self.client = self._authenticate()
        if Config.GOOGLE_SHEET_URL:
            sheet = self.client.open_by_url(Config.GOOGLE_SHEET_URL)
        else:
            sheet = self.client.open(Config.GOOGLE_SHEET_NAME)
        self.worksheet = sheet.worksheet(Config.GOOGLE_SHEET_WORKSHEET)
        self.col_map = self._build_col_map()
        self._ensure_status_columns()

    def _authenticate(self):
        creds_path = Config.GOOGLE_SHEET_CREDENTIALS
        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"Service account credentials not found at: {creds_path}"
            )
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, self.SCOPE)
        return gspread.authorize(creds)

    def _build_col_map(self):
        headers = self.worksheet.row_values(1)
        return {h.strip().lower(): idx + 1 for idx, h in enumerate(headers)}

    def _col_letter(self, col_idx):
        letter = ""
        while col_idx > 0:
            col_idx -= 1
            letter = chr(ord("A") + col_idx % 26) + letter
            col_idx //= 26
        return letter

    def _ensure_status_columns(self):
        headers = self.worksheet.row_values(1)
        needs_update = False
        if self.STATUS_COL not in headers:
            headers.append(self.STATUS_COL)
            needs_update = True
        if self.NOTES_COL not in headers:
            headers.append(self.NOTES_COL)
            needs_update = True
        if needs_update:
            self.worksheet.resize(cols=len(headers))
            self.worksheet.update(f"A1:{self._col_letter(len(headers))}1", [headers])
        self.col_map = self._build_col_map()

    def get_pending_posts(self):
        all_records = self.worksheet.get_all_records()
        pending = []
        for idx, row in enumerate(all_records, start=2):
            status = str(row.get(self.STATUS_COL, "")).strip().lower()
            if status and status != "pending":
                continue

            platform = str(row.get("Platform", "both")).strip().lower()
            if platform in ("all",):
                platform = "all"
            elif platform in ("youtube", "yt"):
                platform = "youtube"
            media_url = str(row.get("Media URL", "")).strip()
            product_url = str(row.get("Product URL", "")).strip()
            product_id = str(row.get("Product ID", "")).strip()
            caption = str(row.get("Caption", "")).strip()
            hashtags = str(row.get("Hashtags", "")).strip()
            full_caption = f"{caption}\n\n{hashtags}" if hashtags else caption
            title = (caption or "Video")[:100]

            if not media_url:
                continue

            pending.append({
                "row": idx,
                "media_url": media_url,
                "product_url": product_url,
                "product_id": product_id,
                "caption": full_caption,
                "title": title,
                "platform": platform,
            })
        return pending

    def update_status(self, row, status, notes=""):
        status_idx = self.col_map.get(self.STATUS_COL.lower())
        notes_idx = self.col_map.get(self.NOTES_COL.lower())
        if status_idx:
            col = self._col_letter(status_idx)
            self.worksheet.update(f"{col}{row}", [[status]])
        if notes_idx and notes:
            col = self._col_letter(notes_idx)
            self.worksheet.update(f"{col}{row}", [[notes]])

    def get_posted_ids(self):
        posted = set()
        if "product id" not in self.col_map:
            return posted
        try:
            records = self.worksheet.get_all_records()
        except Exception as e:
            logger.warning(f"get_posted_ids failed: {e}")
            return posted
        for row in records:
            pid = str(row.get("Product ID", "")).strip()
            status = str(row.get(self.STATUS_COL, "")).strip().lower()
            if pid and status in POSTED_STATUSES:
                posted.add(pid)
        return posted

    def get_caption_override(self, pid, url=""):
        if "product id" not in self.col_map:
            return None, None
        try:
            records = self.worksheet.get_all_records()
        except Exception as e:
            logger.warning(f"get_caption_override failed: {e}")
            return None, None
        for row in records:
            row_pid = str(row.get("Product ID", "")).strip()
            if row_pid != str(pid):
                continue
            caption = str(row.get("Caption", "")).strip()
            hashtags = str(row.get("Hashtags", "")).strip()
            if caption or hashtags:
                return caption or None, hashtags or None
        return None, None

    def mark_posted(self, pid, results):
        headers = self.worksheet.row_values(1)
        next_row = len(self.worksheet.get_all_values()) + 1
        row_data = {
            "Product ID": pid,
            self.STATUS_COL: "uploaded",
            self.NOTES_COL: json.dumps(results),
        }
        values = [row_data.get(h, "") for h in headers]
        try:
            col = self._col_letter(len(headers))
            self.worksheet.update(f"A{next_row}:{col}{next_row}", [values])
        except Exception as e:
            logger.error(f"mark_posted failed for {pid}: {e}")
