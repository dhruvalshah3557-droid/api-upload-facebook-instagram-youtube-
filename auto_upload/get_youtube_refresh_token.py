#!/usr/bin/env python3
import argparse
import json
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


def print_secrets(creds, client_secret, refresh_key):
    if not creds.refresh_token:
        sys.exit(
            "No refresh token returned. Re-run and re-authorize, ensuring the app "
            "requests offline access (access_type=offline)."
        )

    with open(client_secret) as f:
        secret_data = json.load(f)
    client_info = secret_data.get("installed") or secret_data.get("web") or {}
    client_id = client_info.get("client_id", "")
    client_secret_value = client_info.get("client_secret", "")

    print("\n" + "=" * 60)
    print("PASTE THESE INTO GITHUB SECRETS (exact KEY=VALUE pairs)")
    print("=" * 60)
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret_value}")
    print(f"{refresh_key}={creds.refresh_token}")
    print("=" * 60)
    print(
        "Set each KEY=VALUE line as a GitHub Secret (Settings > Secrets and "
        "variables > Actions > New repository secret)."
    )


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
    parser.add_argument(
        "--suffix",
        default="",
        help="Account suffix for the secret key, e.g. JIYA -> "
        "YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Open the consent page in your default browser and capture the "
        "authorization code automatically (best on a machine with a browser).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for the loopback redirect when --local is used "
        "(default: %(default)s)",
    )
    args = parser.parse_args()

    suffix = ("_" + args.suffix) if args.suffix else ""
    refresh_key = f"YOUTUBE_OAUTH_REFRESH_TOKEN{suffix}"

    client_secret = Path(args.client_secret)
    if not client_secret.exists():
        sys.exit(f"Client secret JSON not found at: {client_secret}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)

    if args.local:
        creds = flow.run_local_server(
            port=args.port,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
            authorization_prompt_message="Please visit this URL to authorize:",
            success_message="Authorization successful. You may close this window.",
        )
        print_secrets(creds, client_secret, refresh_key)
        return

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
    print_secrets(flow.credentials, client_secret, refresh_key)


if __name__ == "__main__":
    main()
