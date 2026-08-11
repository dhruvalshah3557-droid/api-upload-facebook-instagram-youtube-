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

    LANG_DESC_COLS = {
        "tl": ["Filipino Description"],
        "my": ["Burmese Description"],
        "th": ["Thai Description"],
        "zh": ["Chinese Description"],
        "ru": ["Russian Description"],
        "ja": ["Japanese Description"],
        "ko": ["Korean Description"],
    }
    LANG_TAG_COLS = {
        "tl": ["Filipino Hashtag"],
        "my": ["Burmese Hashtag"],
        "th": ["Thai Hashtag"],
        "zh": ["Chinese Hashtag"],
        "ru": ["Russian Hashtag"],
        "ja": ["Japanese Hashtag"],
        "ko": ["Korean Hashtag"],
    }

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

    @staticmethod
    def _pick(row, *names):
        for name in names:
            v = row.get(name)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

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
            if not product_id:
                product_id = str(row.get("STK", "")).strip()

            facebook_caption = self._pick(row, "FACEBOOK CAPTION", "Facebook Caption")
            instagram_caption = self._pick(row, "INSTAGRAM CAPTION", "Instagram Caption")
            youtube_caption = self._pick(row, "YouTube Shorts Caption", "TikTok Caption")
            hashtags = self._pick(row, "HASHTAGS", "Hashtags")

            lang_captions = {}
            for lang, cols in self.LANG_DESC_COLS.items():
                lang_captions[lang] = self._pick(row, *cols)
            lang_hashtags = {}
            for lang, cols in self.LANG_TAG_COLS.items():
                lang_hashtags[lang] = self._pick(row, *cols)

            title = (facebook_caption or instagram_caption or "Video")[:100]

            if not media_url:
                continue

            pending.append({
                "row": idx,
                "media_url": media_url,
                "product_url": product_url,
                "product_id": product_id,
                "facebook_caption": facebook_caption,
                "instagram_caption": instagram_caption,
                "youtube_caption": youtube_caption,
                "hashtags": hashtags,
                "lang_captions": lang_captions,
                "lang_hashtags": lang_hashtags,
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
