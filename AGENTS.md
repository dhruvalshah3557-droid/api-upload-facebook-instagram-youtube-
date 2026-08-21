# AGENTS.md

Project facts and conventions for the `auto_upload` project. These are persistent — no need to re-explain.

## Project

- Social-media auto uploader (`auto_upload/`) that publishes to Facebook, Instagram, and YouTube.
- Code: `auto_upload/main.py`, `sheets_reader.py`, `job_generator.py`, `facebook_uploader.py`, `instagram_uploader.py`, `youtube_uploader.py`, `caption_generator.py`, `product_scraper.py`, `config.py`, `media_prep.py`.

## Workbook structure (authoritative)

The uploader reads/writes ONE Google Sheets workbook:
- URL: `https://docs.google.com/spreadsheets/d/1jjC4oaWsyqLzG6vT5EwJkVAgJCXGpz_7fWr6wb7OU3o/edit`
- Sheet ID: `1jjC4oaWsyqLzG6vT5EwJkVAgJCXGpz_7fWr6wb7OU3o`

Tabs (only these):
- `UPLOAD GUIDE` — workflow rules (carousel order, tagging, safety). Read before changing uploader logic.
- `Sheet1` — HUMAN PREVIEW ONLY. The uploader must NOT use Sheet1 as its data source.
- `Source Import` — primary product/media/content source. One row per SKU. Headers in row 1.
- `Accounts` — destination account settings (per-account tokens, language, formats, product_tagging). Headers in row 2 (row 1 blank).
- `Publishing Queue` — upload jobs (read + status updates). Headers in row 2 (row 1 blank).
- `Publishing Log` — upload result history (write-only). Headers in row 2 (row 1 blank).

Flow: Source Import → Accounts → Publishing Queue → Publishing Log.

## Credentials / secrets

- All real credentials are stored in **GitHub Secrets** (repo: `dhruvalshah3557-droid/api-upload-facebook-instagram-youtube-`), NOT in the repo or local env.
- GitHub Secrets are write-only and cannot be read back via API. To access the workbook locally, either:
  - Share the sheet to anyone with the link (public read) and fetch via `https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:json&sheet=<Tab>`, or
  - Paste the service account JSON locally into `auto_upload/credentials/service_account.json` (gitignored).
- GitHub Actions workflow: `.github/workflows/auto-upload.yml` — runs every 2h via cron + manual dispatch, materializes credentials from secrets, runs `python main.py --generate` then `python main.py` (NOT `--loop`; loop never exits in CI).
- Per-account tokens: `Accounts.credential_property_key` names an env var (e.g. `META_TOKEN_FB_ISR`). `Config.get_token()` reads it, falling back to the shared `FB_ACCESS_TOKEN`. The workflow maps each `META_TOKEN_*` to a GitHub secret.
- The workbook is publicly readable: column maps can be re-verified with the gviz endpoint above without credentials.

## Source Import columns (1-based)

- `B` = STK/SKU (the product tag value; never use SR NO). STK arrives as a number (e.g. `298.0`); normalize with `_normalize_sku`.
- `K:R` = product images `image1 link`..`image8 link`; the file whose name contains `center` is the MAIN image (fallback: first nonblank).
- `S` = `video link` (product video → its own Reel/video job; never embedded in carousels).
- `T` = `multiple side image link` (side images; `center.*` URLs are removed).
- `V/W/X` + `Y` = model images (`model image link 1/2/3`, `multiple model photo link`).
- `Z/AA/AB` + `AC` = model videos (`model video link 1/2/3`, `multiple model video link`; AC deduped against Z:AB).
- `AI` = `INSTAGRAM CAPTION`, `AJ` = `FACEBOOK CAPTION`, `BD` = `YouTube Shorts Caption`, `AR` = `HASHTAGS`.
- Per-language: `<Lang> Description` / `<Lang> Hashtag` columns (Burmese, Thai, Filipino, Chinese, Russian, Japanese, Korean, plus israli/spanish/arabic).
- `G` = `LAB` (value `NON CERTIFIED` blocks auto-publish — review only), `Status` = generated-content status (`Error: ...` / `429` rows are skipped as incomplete).

## Accounts sheet

Columns include `account_id`, `platform`, `account_name`, `platform_account_id`, `primary_language`, `timezone`, `enabled`, `allowed_formats`, `min_gap_minutes`, `product_tagging`, `catalog_or_store_id`, `credential_property_key`, `approval_required`.

- Only `enabled = Yes` accounts are used. As of last check, all 13 Facebook accounts (FB-ISR/JPN/KOR/RUS/PH/JIYA/MMR/GLOBAL/BKK/TREND/LTD/CD/NFCD) are enabled; Instagram (IG-BKK/TREND/LTD/CD) and YouTube (YT-CD) accounts are disabled until IG business IDs / OAuth are connected.
- `product_tagging = Yes` means every FB/IG post is tagged with the row's `STK`/SKU (Publishing Queue `stock_id_tag` / `tag_stock_id_used`). A rejected tag → `tagging_status = Failed` + exact API error in `error_message`.

## Publishing Queue

Columns: `job_id, sku, account_id, media_selection, platform, format, language, scheduled_at, timezone, stock_id_tag, status, attempts, last_attempt_at, platform_post_id, published_url, error_message, notes, tagging_status, tag_stock_id_used, caption_final`.

- `media_selection` values: `carousel`, `product_video`, `model_video:<n>`.
- `status` lifecycle: empty/pending → `uploaded` | `failed` (after `MAX_JOB_ATTEMPTS`) | `skipped` | `needs_review`.
- `python main.py --generate` fills the queue idempotently from clean Source Import rows (carousel + product Reel + one job per model video, per enabled account). `python main.py` processes `MAX_JOBS_PER_RUN` (default 40) pending jobs per run.

## Publishing Log

Columns: `job_id, attempt_time, result, platform_post_id, published_url, api_error_code, error_message, next_retry_at, raw_response_reference, notes`. Appended after every attempt.

## Upload behavior

- `main.py` reads pending jobs from Publishing Queue, resolves media from Source Import by SKU, builds the caption per account language, tags with the same SKU, uploads, then writes status back to the queue and an entry to the log.
- Caption precedence (`build_caption`): primary_language translation + matching hashtags → fallback_language translation + matching hashtags → platform caption + hashtags → auto-generated (no cross-language mixing).
- Carousel media order: MAIN center image → side images only. The product video is NEVER in a carousel — it is always its own separate Reel/video job (avoids duplicate posts).
- Optional audio handling (`media_prep.py`): original video audio is preserved by default. When `MIX_BACKGROUND_MUSIC=true` + `BACKGROUND_MUSIC_PATH` are set and a video is silent, a low-volume (0.15) instrumental track is mixed in via ffmpeg before upload (FB video, IG Reel file upload, YouTube).
- Each model video is its OWN Reel/video job. Product video is its own job too.
- Media with video extension (`.mp4`/`.mov`/`.avi`/`.mkv`/`.webm`) → video/Reel; otherwise photo/carousel.
- Instagram videos are posted as REELS with a processing wait. Instagram carousels use the mixed children API (video children use `media_type=VIDEO`, not REELS).
- Facebook carousels: children created via `/{page}/photos` with `published=false`, then published via `/{page}/feed` with `attached_media` + `message`.
- Run modes: `python main.py` (once), `python main.py --generate` (populate queue), `python main.py --loop` (poll every 300s; only for App Engine, not CI), `python main.py --direct` (direct upload via env).
- `auto_upload/dump_pages.py` + `.github/workflows/dump-pages.yml` dump all FB page names/IG ids via `me/accounts` (manual dispatch → `pages` artifact).

## Local dev

- Preview page: `auto_upload/preview.html` — interactive simulation of the pipeline (serve via `python3 -m http.server`).
- `.env` goes in `auto_upload/` (gitignored), keys per `auto_upload/.env.example`.
