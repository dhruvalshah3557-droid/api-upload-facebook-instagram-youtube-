"""Generate listenable voice options in Main Data / Voice Previews."""
import argparse
import datetime as dt
import logging
import os
import hashlib
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
LANG_DESCRIPTION_HEADERS = {
    "ar":"arabic description", "cs":"vestslavic description", "da":"danish description",
    "de":"german description", "es":"spanish description", "fil":"Filipino Description",
    "fr":"french description", "he":"israli description", "id":"Indonesian description",
    "it":"italian description", "ja":"Japanese Description", "ko":"Korean Description",
    "lb":"lebenesse description", "my":"Burmese Description", "pl":"polish description",
    "ru":"Russian Description", "sv":"sweden description", "th":"Thai Description",
    "tl":"Filipino Description", "tr":"turkish description", "ur":"urdu description",
    "vi":"Vietnamese Description", "zh":"Chinese Description", "zh-hant":"Chinese Description",
}

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

def _client():
    creds = Credentials.from_service_account_file(Config.GOOGLE_SHEET_CREDENTIALS, scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds)

def _normalize_sku(value):
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text

def _localized_script(record, language):
    code = language_code(language)
    header = LANG_DESCRIPTION_HEADERS.get(code, "")
    return preview_script(record.get(header) or record.get(header.title()) or record.get("PRODUCT DESCRIPTION") or record.get("DETAILS") or "")

def generate(sku, language="en-GB", client=None, auto_approve=True):
    client = client or _client()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ws = _worksheet(client)
    records = client.open_by_url(Config.MODEL_MEDIA_SHEET_URL).worksheet("jewellery stock ").get_all_records()
    record = next((r for r in records if _normalize_sku(r.get("SKU","")) == _normalize_sku(sku)), None)
    if not record: raise ValueError(f"SKU {sku} not found")
    extras = str(record.get("multiple video link") or "").replace("\r","\n").split("\n")
    media_urls = list(dict.fromkeys(u.strip() for u in [record.get("video link"), *extras] if str(u or "").strip()))
    script = _localized_script(record, language)
    if not media_urls or not script: raise ValueError(f"SKU {sku} needs video in S/U and PRODUCT DESCRIPTION in AE")
    existing = ws.get_all_records()
    for media_url in media_urls:
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder)/"source.mp4"; video.write_bytes(_request_get(media_url, timeout=180, headers=DOWNLOAD_HEADERS).content)
            media_key = hashlib.sha256(media_url.encode("utf-8")).hexdigest()[:8]
            if any(_normalize_sku(r.get("sku")) == _normalize_sku(sku) and str(r.get("media_url")) == media_url and language_code(r.get("language")) == language_code(language) for r in existing):
                continue
            if audio_state(str(video)) == "audible":
                ws.append_row([f"{sku}-{language_code(language)}-{media_key}-original",now,sku,media_url,language,"","",script,"","Original Audio Preserved",now,"",""])
                continue
            for index, voice in enumerate(choose_voices(language), 1):
                preview_id = f"{sku}-{language_code(language)}-{media_key}-{index}"
                output = Path(folder)/f"{preview_id}.mp3"; synthesize(script, voice["ShortName"], output)
                url = _release_asset(output, output.name)
                selected = auto_approve and index == 1
                status = "Approved" if selected else "Pending Approval"
                ws.append_row([preview_id,now,sku,media_url,language,voice["ShortName"],voice.get("Gender",""),script,url,status,now if selected else "","",""])
                logger.info("Created %s", url)

def generate_automatic(limit=2):
    """Generate and auto-select pleasant voices for pending product-video jobs."""
    client = _client()
    ws = _worksheet(client)
    existing = ws.get_all_records()
    existing_pairs = {
        (_normalize_sku(r.get("sku")), language_code(r.get("language")))
        for r in existing if r.get("sku")
    }
    book = client.open_by_url(Config.GOOGLE_SHEET_URL)
    queue = book.worksheet(Config.QUEUE_SHEET).get_all_records()
    candidates = []
    for row in queue:
        status = str(row.get("status") or "pending").strip().lower()
        selection = str(row.get("media_selection") or "")
        pair = (_normalize_sku(row.get("sku")), language_code(row.get("language") or "en"))
        if status not in {"", "pending", "retry"} or not selection.startswith("product_video") or pair in existing_pairs or pair in candidates:
            continue
        candidates.append(pair)
        if len(candidates) >= max(1, int(limit)):
            break
    for sku, language in candidates:
        try:
            generate(sku, language, client=client, auto_approve=True)
        except Exception as exc:
            logger.exception("Automatic preview failed for %s/%s: %s", sku, language, exc)

if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--sku"); parser.add_argument("--language",default="en-GB"); parser.add_argument("--auto-limit",type=int,default=2); args=parser.parse_args()
    generate(args.sku,args.language) if args.sku else generate_automatic(args.auto_limit)
