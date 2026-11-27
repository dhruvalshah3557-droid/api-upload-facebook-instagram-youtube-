#!/usr/bin/env python3
"""Re-authorize a YouTube channel and print a fresh OAuth refresh token.

Runs inside GitHub Actions so it can use the existing OAuth client secrets
without ever exposing them. Two modes:

  url       Print the Google consent URL for the selected channel.
  exchange  Trade the pasted authorization code (or full redirect URL) for a
            refresh token, verify the channel, and print KEY=VALUE to paste
            into GitHub Secrets.

The client secret is never printed.
"""
import argparse
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://127.0.0.1:8080/"

CHANNELS = {
    "COLOURDIAMONDSS": {
        "client_id": "YOUTUBE_CLIENT_ID_COLOURDIAMONDSS",
        "client_secret": "YOUTUBE_CLIENT_SECRET_COLOURDIAMONDSS",
        "refresh_key": "YOUTUBE_OAUTH_REFRESH_TOKEN_COLOURDIAMONDSS",
        "expected_id": "UCTWbcY-YtvAx2QUZKXt230A",
    },
    "JIYA": {
        "client_id": "YOUTUBE_CLIENT_ID",
        "client_secret": "YOUTUBE_CLIENT_SECRET",
        "refresh_key": "YOUTUBE_OAUTH_REFRESH_TOKEN_JIYA",
        "expected_id": "UCDFzWfIRvLu1LXq3mQrkj6Q",
    },
    "TRENDINGJEWELS": {
        "client_id": "YOUTUBE_CLIENT_ID",
        "client_secret": "YOUTUBE_CLIENT_SECRET",
        "refresh_key": "YOUTUBE_OAUTH_REFRESH_TOKEN_TRENDINGJEWELS",
        "expected_id": "UCWSGpmJfBIzcA9O9kFWaRNw",
    },
    "TREND": {
        "client_id": "YOUTUBE_CLIENT_ID",
        "client_secret": "YOUTUBE_CLIENT_SECRET",
        "refresh_key": "YOUTUBE_OAUTH_REFRESH_TOKEN_TREND",
        "expected_id": "UCiOkTy5C8_dZw-TlzYKeOVA",
    },
}


def extract_code(value):
    value = (value or "").strip()
    if "code=" in value:
        params = parse_qs(urlsplit(value).query)
        if "code" in params:
            return params["code"][0]
    return value


def build_flow(spec):
    client_id = os.getenv(spec["client_id"], "").strip()
    client_secret = os.getenv(spec["client_secret"], "").strip()
    if not client_id or not client_secret:
        sys.exit(
            f"Missing OAuth client credentials: set {spec['client_id']} and "
            f"{spec['client_secret']} in GitHub Secrets."
        )
    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        config,
        SCOPES,
        redirect_uri=REDIRECT_URI,
        # PKCE verifiers are per-process. The URL and exchange steps run in
        # separate workflow runs, so a generated verifier would be lost and the
        # exchange would fail with invalid_grant. Disable it for stateless use.
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = REDIRECT_URI
    return flow


def verify_channel(creds, spec, label):
    service = build("youtube", "v3", credentials=creds)
    items = service.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if not items:
        sys.exit("No YouTube channel found for the authorized Google account.")
    channel = items[0]
    channel_id = channel.get("id", "")
    title = channel.get("snippet", {}).get("title", "")
    if channel_id != spec["expected_id"]:
        sys.exit(
            f"Wrong channel authorized: {title} ({channel_id}). "
            f"Expected {spec['expected_id']} for {label}. "
            "Sign in with the Google account that owns this channel."
        )
    print(f"Verified channel: {title} ({channel_id})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    parser.add_argument("--mode", required=True, choices=["url", "exchange"])
    parser.add_argument("--code", default="")
    args = parser.parse_args()

    spec = CHANNELS[args.channel]
    flow = build_flow(spec)

    if args.mode == "url":
        auth_url, _ = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="true"
        )
        print("=" * 70)
        print(f"Authorize {args.channel} - open this URL in a browser:")
        print("=" * 70)
        print(auth_url)
        print("=" * 70)
        print(
            "After approving, the browser redirects to 127.0.0.1:8080 and shows "
            "an error page. Copy the FULL address bar URL (it contains code=...)."
        )
        print(
            "Then re-run this workflow with mode=exchange and paste that URL "
            f"into the 'code' input."
        )
        return

    code = extract_code(args.code)
    if not code:
        sys.exit("exchange mode requires --code (authorization code or redirect URL).")
    flow.fetch_token(code=code)
    verify_channel(flow.credentials, spec, args.channel)

    if not flow.credentials.refresh_token:
        sys.exit(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and try again."
        )

    print("\n" + "=" * 70)
    print("PASTE THIS INTO GITHUB SECRETS (Settings > Secrets > Actions)")
    print("=" * 70)
    print(f"{spec['refresh_key']}={flow.credentials.refresh_token}")
    print("=" * 70)


if __name__ == "__main__":
    main()
