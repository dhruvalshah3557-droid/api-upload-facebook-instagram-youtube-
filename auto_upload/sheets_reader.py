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
            platform = "both" if platform in ("all", "both") else platform
            media_url = str(row.get("Media URL", "")).strip()
            caption = str(row.get("Caption", "")).strip()
            hashtags = str(row.get("Hashtags", "")).strip()
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
        status_idx = self.col_map.get(self.STATUS_COL.lower())
        notes_idx = self.col_map.get(self.NOTES_COL.lower())
        if status_idx:
            col = self._col_letter(status_idx)
            self.worksheet.update(f"{col}{row}", [[status]])
        if notes_idx and notes:
            col = self._col_letter(notes_idx)
            self.worksheet.update(f"{col}{row}", [[notes]])
