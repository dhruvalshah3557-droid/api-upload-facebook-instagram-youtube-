import os
import pickle
import logging
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import requests
import io
import mimetypes

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "credentials/youtube_token.pickle"
CLIENT_SECRET_FILE = "credentials/youtube_client_secret.json"


class YouTubeUploader:
    def __init__(self):
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(CLIENT_SECRET_FILE):
                    raise FileNotFoundError(
                        f"YouTube OAuth client secret not found at: {CLIENT_SECRET_FILE}\n"
                        "1. Go to https://console.cloud.google.com/apis/credentials\n"
                        "2. Create OAuth 2.0 Client ID (Desktop app)\n"
                        "3. Download JSON and save as credentials/youtube_client_secret.json"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)

        return build("youtube", "v3", credentials=creds)

    def _download_video(self, url):
        logger.info(f"Downloading video for YouTube: {url}")
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        return io.BytesIO(resp.content)

    def upload(self, media_url, title, description="", tags=None):
        logger.info("Uploading to YouTube...")
        video_content = self._download_video(media_url)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags or [],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        mime_type, _ = mimetypes.guess_type(media_url)
        if not mime_type:
            mime_type = "video/mp4"

        media = MediaIoBaseUpload(video_content, mimetype=mime_type, resumable=True)

        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = request.execute()
        video_id = response.get("id")
        logger.info(f"YouTube uploaded: https://youtu.be/{video_id}")
        return response
