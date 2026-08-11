# AGENTS.md

Project facts and conventions for the `auto_upload` project. These are persistent — no need to re-explain.

## Project

- Social-media auto uploader (`auto_upload/`) that reads pending posts from a Google Sheet and publishes to Facebook, Instagram, and YouTube.
- Code: `auto_upload/main.py`, `sheets_reader.py`, `facebook_uploader.py`, `instagram_uploader.py`, `youtube_uploader.py`, `caption_generator.py`, `product_scraper.py`, `config.py`.

## Credentials / secrets

- All real credentials are stored in **GitHub Secrets** (repo: `dhruvalshah3557-droid/api-upload-facebook-instagram-youtube-`), NOT in the repo or local env.
- GitHub Secrets are write-only and cannot be read back via API. To access the Google Sheet locally, either:
  - Share the sheet to anyone with the link (public read) and fetch via `https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:json`, or
  - Paste the service account JSON locally into `auto_upload/credentials/service_account.json` (gitignored).
- GitHub Actions workflow: `.github/workflows/auto-upload.yml` — runs every 6h via cron + manual dispatch, materializes credentials from secrets, runs `python main.py` (NOT `--loop`; loop never exits in CI).

## Google Sheet

- Sheet URL: `https://docs.google.com/spreadsheets/d/1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ/edit`
- Sheet ID: `1kAD1ASXaaqrBmNHDVMYgj_cfW8pFJPEiRCY8ENutAvQ`
- ~999 rows of diamond jewellery stock; 101 columns (per-product SEO captions, multi-language descriptions, etc.).

## Page languages

The auto-uploader supports per-page languages for auto-generated captions/hashtags in `caption_generator.py`.

Supported languages: `en`, `th`, `my`, `tl`, `zh`, `ru`, `ja`, `ko`.

Page-name → language mapping (`PAGE_LANG_MAP` in `caption_generator.py`):
- `colour diam philippines` / `colour diam ph` → Filipino (`tl`)
- `colour diam myanmar` → Burmese (`my`)
- `colour diam bangkok` / `colour diam thailand` → Thai (`th`)
- `colour diam china` / `taiwan` / `hong kong` → Chinese (`zh`)
- `colour diam russia` → Russian (`ru`)
- `colour diam japan` / `japanese` → Japanese (`ja`)
- `colour diam korea` / `korean` → Korean (`ko`)
- default (Trending Jewel, Colour Diam, etc.) → English (`en`)

Sheet columns used for per-language content: `Burmese Description`/`Burmese Hashtag`, `Thai Description`/`Thai Hashtag`, `Filipino Description`/`Filipino Hashtag`, `Chinese Description`/`Chinese Hashtag`, `Russian Description`/`Russian Hashtag`, `Japanese Description`/`Japanese Hashtag`, `Korean Description`/`Korean Hashtag`.

## Caption / hashtag column mapping

The sheet has no `Caption` column. `sheets_reader.py` reads platform + language captions per row; `main.py` picks per page language/platform:
- Facebook caption ← `FACEBOOK CAPTION` (fallback `Facebook Caption`)
- Instagram caption ← `INSTAGRAM CAPTION` (fallback `Instagram Caption`)
- YouTube caption ← `YouTube Shorts Caption` (fallback `TikTok Caption`)
- Hashtags ← `HASHTAGS` (fallback `Hashtags`)
- Per-language: `lang_captions`/`lang_hashtags` ← `<Lang> Description`/`<Lang> Hashtag` columns (only ~24 rows have per-language hashtags; descriptions are filled for ~618)
- Caption precedence in `make_page_caption` (`main.py`): page-language column → platform caption + hashtags → auto-generated (page language).
- Per-language content counts (of 999 rows): Burmese/Thai/Filipino descriptions 618; Chinese/Russian/Japanese/Korean 24-48.

## Upload behavior

- `main.py` reads rows where `Status` is empty or `pending`, dispatches by `Platform` column (`facebook`/`instagram`/`both`/`youtube`/`all`), uploads, then writes `uploaded`/`failed` back to the sheet.
- Product tagging: `product_id` comes from the `Product ID` column; if empty, falls back to the `STK` column (stock ID, ~487/999 rows, matches `productdetail/<STK>` URLs). Sent as `product_tags` to FB/IG. `Product Tags` column is SEO keywords, NOT used for tagging.
- Media URL with video extension (`.mp4`/`.mov`/`.avi`/`.mkv`/`.webm`) → uploaded as video; otherwise as photo.
- Instagram videos are posted as REELS with a 30s processing wait.
- Run modes: `python main.py` (once) or `python main.py --loop` (poll every 300s; only for App Engine, not CI).
- `auto_upload/dump_pages.py` + `.github/workflows/dump-pages.yml` dump all FB page names/IG ids via `me/accounts` (manual dispatch → `pages` artifact).

## Local dev

- Preview page: `auto_upload/preview.html` — interactive simulation of the pipeline (serve via `python3 -m http.server`).
- `.env` goes in `auto_upload/` (gitignored), keys per `auto_upload/.env.example`.
