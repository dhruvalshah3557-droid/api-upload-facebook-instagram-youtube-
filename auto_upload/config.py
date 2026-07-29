import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()


class Config:
    GOOGLE_SHEET_CREDENTIALS = os.getenv("GOOGLE_SHEET_CREDENTIALS", "credentials/service_account.json")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
    GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "AutoUpload")
    GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Sheet1")

    FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    USE_SECRET_MANAGER = os.getenv("USE_SECRET_MANAGER", "false").lower() == "true"

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
