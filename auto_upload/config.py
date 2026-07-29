import os
import json
from dotenv import load_dotenv

load_dotenv()


class Config:
    GOOGLE_SHEET_CREDENTIALS = os.getenv("GOOGLE_SHEET_CREDENTIALS", "credentials/service_account.json")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "")
    GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "AutoUpload")
    GOOGLE_SHEET_WORKSHEET = os.getenv("GOOGLE_SHEET_WORKSHEET", "Sheet1")

    FB_PAGE_ID = os.getenv("FB_PAGE_ID")
    FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
    IG_USER_ID = os.getenv("IG_USER_ID")

    GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    USE_SECRET_MANAGER = os.getenv("USE_SECRET_MANAGER", "false").lower() == "true"
