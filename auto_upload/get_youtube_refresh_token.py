#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_CLIENT_SECRET = Path(__file__).parent / "credentials" / "youtube_client_secret.json"
DEFAULT_AUTH_CODE_FILE = Path(__file__).parent / "credentials" / "youtube_auth_code.txt"
LOOPBACK_REDIRECT_URI = "http://127.0.0.1:8080/"


def extract_code(value):
    value = value.strip()
    if "code=" in value:
        params = parse_qs(urlsplit(value).query)
        if "code" in params:
            return params["code"][0]
    return value


def main():
    parser = argparse.ArgumentParser(
        description="Obtain a YouTube OAuth refresh token from a client secret JSON "
        "and an authorization code."
    )
    parser.add_argument(
        "--client-secret",
        default=str(DEFAULT_CLIENT_SECRET),
        help="Path to the OAuth client secret JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--code",
        default="",
        help="Authorization code (raw code or full redirect URL). If omitted, read "
        "from the auth code file or a prompt.",
    )
    parser.add_argument(
        "--auth-code-file",
        default=str(DEFAULT_AUTH_CODE_FILE),
        help="File containing the authorization code (default: %(default)s)",
    )
    args = parser.parse_args()

    client_secret = Path(args.client_secret)
    if not client_secret.exists():
        sys.exit(f"Client secret JSON not found at: {client_secret}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    flow.redirect_uri = LOOPBACK_REDIRECT_URI
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    code = extract_code(args.code)
    if not code:
        auth_code_file = Path(args.auth_code_file)
        if auth_code_file.exists():
            code = extract_code(auth_code_file.read_text())
    if not code:
        print("=" * 60)
        print("YOUTUBE AUTH REQUIRED - Visit this URL in a browser:")
        print("=" * 60)
        print(auth_url)
        print("=" * 60)
        print("After authorizing, paste the code (or the full redirect URL) here:")
        code = extract_code(input("code> "))

    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        sys.exit(
            "No refresh token returned. Re-run and re-authorize, ensuring the app "
            "requests offline access (access_type=offline)."
        )

    print("\n" + "=" * 60)
    print("YOUTUBE_OAUTH_REFRESH_TOKEN")
    print("=" * 60)
    print(creds.refresh_token)
    print("=" * 60)
    print(
        "Set this value plus YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET as GitHub "
        "Secrets so CI can upload."
    )


if __name__ == "__main__":
    main()
