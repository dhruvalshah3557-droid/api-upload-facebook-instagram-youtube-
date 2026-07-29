import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from config import Config


class SheetsReader:
    SCOPE = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self):
        self.client = self._authenticate()
        sheet = self.client.open(Config.GOOGLE_SHEET_NAME)
        self.worksheet = sheet.worksheet(Config.GOOGLE_SHEET_WORKSHEET)

    def _authenticate(self):
        creds_path = Config.GOOGLE_SHEET_CREDENTIALS
        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"Google service account credentials not found at: {creds_path}"
            )
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, self.SCOPE)
        return gspread.authorize(creds)

    def get_pending_posts(self):
        all_records = self.worksheet.get_all_records()
        pending = []
        for idx, row in enumerate(all_records, start=2):
            status = str(row.get("status", "")).strip().lower()
            if status == "pending":
                pending.append({
                    "row": idx,
                    "media_url": str(row.get("media_url", "")).strip(),
                    "caption": str(row.get("caption", "")).strip(),
                    "platform": str(row.get("platform", "both")).strip().lower(),
                })
        return pending

    def update_status(self, row, status, notes=""):
        self.worksheet.update(f"E{row}", [[status]])
        if notes:
            self.worksheet.update(f"F{row}", [[notes]])
