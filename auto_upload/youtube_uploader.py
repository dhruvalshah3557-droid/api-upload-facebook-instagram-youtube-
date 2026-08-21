import pickle
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import requests
import io
import mimetypes

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CRED_DIR = Path(__file__).parent / "credentials"
TOKEN_FILE = CRED_DIR / "youtube_token.pickle"
CLIENT_SECRET_FILE = CRED_DIR / "youtube_client_secret.json"
AUTH_CODE_FILE = CRED_DIR / "youtube_auth_code.txt"
LOOPBACK_REDIRECT_URI = "http://127.0.0.1:8080/"


def _extract_code(value):
    """Extract the auth code from a pasted value (raw code or full redirect URL)."""
    value = value.strip()
    if "code=" in value:
        params = parse_qs(urlsplit(value).query)
        if "code" in params:
            return params["code"][0]
    return value


class YouTubeUploader:
    def __init__(self):
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None
        if TOKEN_FILE.exists():
            with open(TOKEN_FILE, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not CLIENT_SECRET_FILE.exists():
                    raise FileNotFoundError(
                        f"YouTube OAuth client secret not found at: {CLIENT_SECRET_FILE}"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CLIENT_SECRET_FILE), SCOPES
                )
                flow.redirect_uri = LOOPBACK_REDIRECT_URI
                auth_url, _ = flow.authorization_url(
                    access_type="offline", include_granted_scopes="true"
                )
                print("\n" + "=" * 60)
                print("YOUTUBE AUTH REQUIRED - Visit this URL:")
                print("=" * 60)
                print(auth_url)
                print("\nAfter authorizing, paste the code in this file:")
                print(str(AUTH_CODE_FILE))
                print("=" * 60 + "\n")

                if not AUTH_CODE_FILE.exists():
                    raise RuntimeError(
                        f"Please:\n"
                        f"1. Open the URL above in your browser\n"
                        f"2. Authorize the app\n"
                        f"3. Copy the code and save it to:\n"
                        f"   {AUTH_CODE_FILE}\n"
                        f"4. Run the tool again"
                    )
                with open(AUTH_CODE_FILE) as f:
                    code = _extract_code(f.read())
                creds = flow.fetch_token(code=code)
                creds = flow.credentials
                AUTH_CODE_FILE.unlink(missing_ok=True)
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)

        return build("youtube", "v3", credentials=creds)

    def _download_video(self, url):
        logger.info(f"Downloading video for YouTube: {url}")
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        return io.BytesIO(resp.content), resp.headers.get("Content-Type", "")

    def upload(self, media_url, title, description="", tags=None):
        logger.info("Uploading to YouTube...")
        video_content, content_type = self._download_video(media_url)

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

        mime_type = self._resolve_mime_type(media_url, content_type)

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

    @staticmethod
    def _resolve_mime_type(media_url, content_type):
        """Pick a MIME type from the download's Content-Type or the URL's extension."""
        if content_type and content_type.split(";")[0].strip().startswith("video/"):
            return content_type.split(";")[0].strip()
        guessed, _ = mimetypes.guess_type(media_url.split("?")[0])
        return guessed or "video/mp4"
