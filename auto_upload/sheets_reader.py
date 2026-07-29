import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from config import Config


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

    def _ensure_status_columns(self):
        headers = self.worksheet.row_values(1)
        if self.STATUS_COL not in headers:
            col_letter = chr(ord("A") + len(headers))
            self.worksheet.update(f"{col_letter}1", self.STATUS_COL)
            headers.append(self.STATUS_COL)
        if self.NOTES_COL not in headers:
            col_letter = chr(ord("A") + len(headers))
            self.worksheet.update(f"{col_letter}1", self.NOTES_COL)
        self.col_map = self._build_col_map()

    def _col_letter(self, col_name):
        idx = self.col_map.get(col_name.lower())
        if not idx:
            return None
        return chr(ord("A") + idx - 1)

    def get_pending_posts(self):
        all_records = self.worksheet.get_all_records()
        status_col_header = self.STATUS_COL.lower()
        pending = []
        for idx, row in enumerate(all_records, start=2):
            status = str(row.get(status_col_header, "")).strip().lower()
            if status and status != "pending":
                continue

            platform = str(row.get("platform", "both")).strip().lower()
            media_url = str(row.get("media url", "")).strip()
            caption = str(row.get("caption", "")).strip()
            hashtags = str(row.get("hashtags", "")).strip()
            full_caption = f"{caption}\n\n{hashtags}" if hashtags else caption

            if not media_url:
                continue

            pending.append({
                "row": idx,
                "media_url": media_url,
                "caption": full_caption,
                "platform": platform,
            })
        return pending

    def update_status(self, row, status, notes=""):
        status_col = self._col_letter(self.STATUS_COL)
        notes_col = self._col_letter(self.NOTES_COL)
        if status_col:
            self.worksheet.update(f"{status_col}{row}", status)
        if notes_col and notes:
            self.worksheet.update(f"{notes_col}{row}", notes)
