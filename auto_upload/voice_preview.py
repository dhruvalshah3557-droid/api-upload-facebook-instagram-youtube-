"""Generate listenable voice options in Main Data / Voice Previews."""
import argparse
import datetime as dt
import logging
import os
import tempfile
from pathlib import Path
import gspread
import requests
from google.oauth2.service_account import Credentials
from config import Config
from media_prep import DOWNLOAD_HEADERS, _request_get, audio_state
from voiceover import choose_voices, language_code, preview_script, synthesize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
HEADERS = ["preview_id","created_at_utc","sku","media_url","language","voice_name","voice_gender","script","preview_url","status","approved_at_utc","final_video_url","error_message"]

def _worksheet(client):
    book = client.open_by_url(Config.MODEL_MEDIA_SHEET_URL)
    try:
        return book.worksheet("Voice Previews")
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title="Voice Previews", rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS); ws.freeze(rows=1)
        return ws

def _release_asset(path, asset_name):
    token, repo = os.getenv("GITHUB_TOKEN", "").strip(), os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY unavailable")
    headers = {"Authorization": f"Bearer {token}", "Accept":"application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}"
    response = requests.get(f"{api}/releases/tags/voice-previews", headers=headers, timeout=30)
    if response.status_code == 404:
        response = requests.post(f"{api}/releases", headers=headers, json={"tag_name":"voice-previews","name":"Voice previews","prerelease":True}, timeout=30)
    response.raise_for_status(); release = response.json()
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            requests.delete(f"{api}/releases/assets/{asset['id']}", headers=headers, timeout=30).raise_for_status()
    with open(path, "rb") as handle:
        uploaded = requests.post(release["upload_url"].split("{",1)[0], headers={**headers,"Content-Type":"audio/mpeg"}, params={"name":asset_name}, data=handle, timeout=120)
    uploaded.raise_for_status()
    return uploaded.json()["browser_download_url"]

def generate(sku, language="en-GB"):
    creds = Credentials.from_service_account_file(Config.GOOGLE_SHEET_CREDENTIALS, scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    client, now = gspread.authorize(creds), dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ws = _worksheet(client)
    records = client.open_by_url(Config.MODEL_MEDIA_SHEET_URL).worksheet("jewellery stock ").get_all_records()
    record = next((r for r in records if str(r.get("SKU","")).strip() == str(sku).strip()), None)
    if not record: raise ValueError(f"SKU {sku} not found")
    extras = str(record.get("multiple video link") or "").replace("\r","\n").split("\n")
    media_urls = list(dict.fromkeys(u.strip() for u in [record.get("video link"), *extras] if str(u or "").strip()))
    script = preview_script(record.get("PRODUCT DESCRIPTION") or record.get("DETAILS") or "")
    if not media_urls or not script: raise ValueError(f"SKU {sku} needs video in S/U and PRODUCT DESCRIPTION in AE")
    existing = ws.get_all_records()
    for media_url in media_urls:
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder)/"source.mp4"; video.write_bytes(_request_get(media_url, timeout=180, headers=DOWNLOAD_HEADERS).content)
            if audio_state(str(video)) == "audible":
                ws.append_row([f"{sku}-{language_code(language)}-original",now,sku,media_url,language,"","",script,"","Original Audio Preserved","","",""])
                continue
            for index, voice in enumerate(choose_voices(language), 1):
                preview_id = f"{sku}-{language_code(language)}-{index}"
                if any(str(r.get("preview_id")) == preview_id and str(r.get("media_url")) == media_url for r in existing): continue
                output = Path(folder)/f"{preview_id}.mp3"; synthesize(script, voice["ShortName"], output)
                url = _release_asset(output, output.name)
                ws.append_row([preview_id,now,sku,media_url,language,voice["ShortName"],voice.get("Gender",""),script,url,"Pending Approval","","",""])
                logger.info("Created %s", url)

if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--sku",required=True); parser.add_argument("--language",default="en-GB"); args=parser.parse_args(); generate(args.sku,args.language)
