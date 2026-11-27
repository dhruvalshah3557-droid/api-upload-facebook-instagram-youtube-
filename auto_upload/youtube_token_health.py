#!/usr/bin/env python3
"""Proactively check every YouTube OAuth refresh token.

Google caps refresh tokens from an OAuth consent screen in "Testing" mode at
7 days. A published (Production/Internal) consent screen issues tokens that do
not expire from age. This script detects dead tokens before they burn upload
slots and prints exactly which GitHub Secret needs re-authorizing.

Exit codes:
  0  every configured YouTube token is healthy
  1  at least one token is dead or missing (details on stdout)
  2  configuration problem (no accounts / Sheets unreachable)

No secrets are printed. Only the secret *name* and the Google error code.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKBOOK_ID = "1jjC4oaWsyqLzG6vT5EwJkVAgJCXGpz_7fWr6wb7OU3o"
GVIZ_URL = "https://docs.google.com/spreadsheets/d/%s/gviz/tq" % WORKBOOK_ID
ACCOUNTS_SHEET = os.environ.get("ACCOUNTS_SHEET", "Accounts")
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Maps a credential_property_key to the OAuth client secret pair it uses.
# YT-JIYA / TREND / TRENDJEWEL share the default client; YT-CD has its own.
CLIENT_KEYS = {
    "YOUTUBE_OAUTH_REFRESH_TOKEN_COLOURDIAMONDSS": (
        "YOUTUBE_CLIENT_ID_COLOURDIAMONDSS",
        "YOUTUBE_CLIENT_SECRET_COLOURDIAMONDSS",
    ),
}
DEFAULT_CLIENT_KEYS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET")


def gviz_rows(sheet_name):
    qs = urllib.parse.urlencode({"sheet": sheet_name, "tqx": "out:json"})
    req = urllib.request.Request(GVIZ_URL + "?" + qs)
    req.add_header("User-Agent", "Mozilla/5.0 youtube-token-health")
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    start = text.find("(")
    end = text.rfind(")")
    payload = json.loads(text[start + 1:end])
    table = payload.get("table", {})
    headers = [c.get("label") or "" for c in table.get("cols", [])]
    rows = []
    for row in table.get("rows", []):
        values = []
        for cell in row.get("c", []):
            values.append("" if cell is None else cell.get("v", ""))
        rows.append(dict(zip(headers, values)))
    return rows


def client_pair(refresh_key):
    return CLIENT_KEYS.get(refresh_key, DEFAULT_CLIENT_KEYS)


def check_token(refresh_key):
    """Return (ok, detail). detail is a short, secret-free explanation."""
    refresh_token = (os.getenv(refresh_key) or "").strip()
    if not refresh_token:
        return False, "secret is not set"
    client_id_key, client_secret_key = client_pair(refresh_key)
    client_id = (os.getenv(client_id_key) or "").strip()
    client_secret = (os.getenv(client_secret_key) or "").strip()
    if not client_id or not client_secret:
        return False, "OAuth client id/secret missing (%s / %s)" % (
            client_id_key, client_secret_key)
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            body = {}
        error = body.get("error", "http_%s" % exc.code)
        return False, "%s" % error
    except Exception as exc:  # network / DNS
        return False, "request failed: %s" % type(exc).__name__
    if body.get("access_token"):
        return True, "ok"
    return False, body.get("error", "no access_token in response")


def main():
    try:
        rows = gviz_rows(ACCOUNTS_SHEET)
    except Exception as exc:
        print("ERROR: could not read Accounts sheet: %s" % exc)
        return 2

    targets = []
    for row in rows:
        if str(row.get("platform", "")).strip().lower() != "youtube":
            continue
        if str(row.get("enabled", "")).strip().lower() not in ("yes", "true", "1"):
            continue
        targets.append({
            "account_id": str(row.get("account_id", "")).strip(),
            "refresh_key": str(row.get("credential_property_key", "")).strip()
            or "YOUTUBE_OAUTH_REFRESH_TOKEN",
        })

    if not targets:
        print("No enabled YouTube accounts found.")
        return 0

    dead = []
    print("YouTube token health (%d account(s)):" % len(targets))
    for target in targets:
        ok, detail = check_token(target["refresh_key"])
        status = "OK" if ok else "DEAD"
        print("  [%s] %-16s %s (%s)" % (
            status, target["account_id"], target["refresh_key"], detail))
        if not ok:
            dead.append(target)

    print()
    if not dead:
        print("All YouTube tokens are healthy.")
        return 0

    print("DEAD TOKENS: %d" % len(dead))
    print(
        "Re-authorize each with the 'Reauthorize YouTube Channel' workflow "
        "(mode=url, then mode=exchange)."
    )
    print(
        "Permanent fix: publish the OAuth consent screen in Google Cloud "
        "Console (APIs & Services > OAuth consent screen > Publish app). "
        "Testing-mode refresh tokens expire after 7 days; published ones do not."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
