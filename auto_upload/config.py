import os

import requests
from dotenv import load_dotenv

load_dotenv()

NEW_WORKBOOK_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1jjC4oaWsyqLzG6vT5EwJkVAgJCXGpz_7fWr6wb7OU3o/edit"
)


class Config:
    GOOGLE_SHEET_CREDENTIALS = os.getenv("GOOGLE_SHEET_CREDENTIALS", "credentials/service_account.json")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", NEW_WORKBOOK_URL)
    GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "")

    SOURCE_IMPORT_SHEET = os.getenv("SOURCE_IMPORT_SHEET", "Source Import")
    ACCOUNTS_SHEET = os.getenv("ACCOUNTS_SHEET", "Accounts")
    QUEUE_SHEET = os.getenv("QUEUE_SHEET", "Publishing Queue")
    LOG_SHEET = os.getenv("LOG_SHEET", "Publishing Log")

    FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN") or os.getenv("FACEBOOKTOKEN") or os.getenv("FACEBOOKDEBUGTOKEN")

    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    USE_SECRET_MANAGER = os.getenv("USE_SECRET_MANAGER", "false").lower() == "true"

    MAX_JOB_ATTEMPTS = int(os.getenv("MAX_JOB_ATTEMPTS", "3"))
    MAX_JOBS_PER_RUN = int(os.getenv("MAX_JOBS_PER_RUN", "40"))
    JOB_STATUS_FAILED = "failed"
    JOB_STATUS_UPLOADED = "uploaded"
    JOB_STATUS_SKIPPED = "skipped"
    JOB_STATUS_NEEDS_REVIEW = "needs_review"

    @classmethod
    def get_token(cls, credential_property_key=""):
        """Resolve an account token by its credential property key.

        Falls back to the shared FB_ACCESS_TOKEN so accounts that share the
        same user-level token still work without per-account secrets.
        """
        if credential_property_key:
            token = os.getenv(credential_property_key)
            if token:
                return token.strip()
        return cls.FB_ACCESS_TOKEN

    @classmethod
    def get_pages(cls):
        if not cls.FB_ACCESS_TOKEN:
            return []
        resp = requests.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            params={"access_token": cls.FB_ACCESS_TOKEN},
            timeout=15,
        )
        data = resp.json()
        pages = []
        for page in data.get("data", []):
            page_id = page["id"]
            page_token = page["access_token"]
            page_name = page.get("name", "")
            ig_resp = requests.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": cls.FB_ACCESS_TOKEN,
                },
                timeout=15,
            )
            ig_data = ig_resp.json()
            ig_user_id = ig_data.get("instagram_business_account", {}).get("id", "")
            pages.append({
                "name": page_name,
                "page_id": page_id,
                "page_token": page_token,
                "ig_user_id": ig_user_id,
            })
        return pages
