import logging
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")

_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"RIFF", "WebP/RIFF"),
)

_VIDEO_MAGIC = (
    (b"\x1a\x45\xdf\xa3", "WebM/MKV"),
    (b"RIFF", "AVI"),
)

_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp")
_VIDEO_TYPES = (
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/webm",
    "video/x-matroska",
    "video/mov",
    "video/mpeg",
)

REELS_WIDTH, REELS_HEIGHT = 1080, 1920
SILENCE_THRESHOLD_DB = -45.0
_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg")
_CC0_SOURCE = (
    "https://raw.githubusercontent.com/effacestudios/"
    "Royalty-Free-Music-Pack/2ce8458293fe4eeb91414a19d6d7ecd1562a5949"
)
BUNDLED_CC0_MUSIC_URLS = (
    f"{_CC0_SOURCE}/Cinemato.mp3",
    f"{_CC0_SOURCE}/Newness.mp3",
    f"{_CC0_SOURCE}/Mysterious.mp3",
    f"{_CC0_SOURCE}/Planning.mp3",
    f"{_CC0_SOURCE}/Illusionist.mp3",
    f"{_CC0_SOURCE}/slow%20down.mp3",
)


def _env(key, default=""):
    value = os.getenv(key)
    return value.strip() if value is not None and value.strip() else default


def _env_true(key, default="true"):
    return _env(key, default).lower() in ("true", "1", "yes", "on")


def _download_bytes(media_url):
    """Download the original file bytes."""
    resp = requests.get(media_url, timeout=180, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    name = media_url.split("?")[0].rsplit("/", 1)[-1] or "media"
    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    return name, resp.content, content_type


def _probe_duration(video_path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 30.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", video_path],
            check=False, capture_output=True, text=True, timeout=60,
        )
        return max(0.5, float((out.stdout or "").strip()))
    except Exception:
        return 30.0


def audio_state(video_path):
    """Return audible, silent, missing, or unknown for a local video.

    This detects both videos with no audio stream and videos that technically
    contain an audio stream but are effectively muted/silent.
    """
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe:
        return "unknown"
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
            check=False, capture_output=True, text=True, timeout=60,
        )
        if not (probe.stdout or "").strip():
            return "missing"
        if not ffmpeg:
            return "unknown"

        detect = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-t", "30", "-i", video_path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
            check=False, capture_output=True, text=True, timeout=90,
        )
        text = (detect.stderr or "") + "\n" + (detect.stdout or "")
        match = re.search(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", text, flags=re.I)
        if not match:
            return "unknown"
        raw = match.group(1).lower()
        if raw in ("-inf", "inf"):
            return "silent"
        max_db = float(raw)
        return "silent" if max_db <= SILENCE_THRESHOLD_DB else "audible"
    except Exception as exc:
        logger.warning(f"Audio analysis failed: {exc}")
        return "unknown"


def _download_music_url(url, out_path):
    resp = requests.get(url, timeout=180, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    Path(out_path).write_bytes(resp.content)
    return out_path


def _stable_choice(items, media_key):
    """Choose repeatably from a library while distributing different videos."""
    digest = hashlib.sha256(media_key.encode("utf-8", errors="ignore")).digest()
    return items[int.from_bytes(digest[:8], "big") % len(items)]


def _configured_music_urls():
    raw = _env("BACKGROUND_MUSIC_URLS", "")
    return [part.strip() for part in re.split(r"[\n,]+", raw) if part.strip()]


def _music_url_library():
    """Merge configured licensed tracks with the pinned CC0 fallback library."""
    return list(dict.fromkeys(_configured_music_urls() + list(BUNDLED_CC0_MUSIC_URLS)))


def _music_files(path):
    root = Path(path)
    if root.is_file() and root.suffix.lower() in _AUDIO_EXTS:
        return [root]
    if root.is_dir():
        return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _AUDIO_EXTS)
    return []


def _resolve_music(media_key, temp_paths):
    """Choose real music from configured sources or a pinned CC0 library."""
    audio_urls = _music_url_library()
    local_path = _env("BACKGROUND_MUSIC_PATH", "")

    if audio_urls:
        audio_url = _stable_choice(audio_urls, media_key)
        fd, path = tempfile.mkstemp(suffix=".music")
        os.close(fd)
        temp_paths.append(path)
        _download_music_url(audio_url, path)
        source = "configured licensed" if audio_url in _configured_music_urls() else "pinned CC0"
        track_id = Path(audio_url.split("?")[0]).name
        logger.info(
            "Selected %s music %d/%d track=%s",
            source, audio_urls.index(audio_url) + 1, len(audio_urls), track_id,
        )
        return path

    music_files = _music_files(local_path) if local_path else []
    if music_files:
        selected = _stable_choice(music_files, media_key)
        logger.info("Selected configured music track: %s", selected.name)
        return str(selected)

    raise RuntimeError("No usable background music source is configured")


def _mix_music(video_path, music_path, out_path, media_key, volume=0.72):
    """Attach background music to a silent/muted video."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to add audio to silent video")
    video_duration = _probe_duration(video_path)
    music_duration = _probe_duration(music_path)
    usable_start = max(0.0, music_duration - video_duration - 1.0)
    digest = hashlib.sha256((media_key + "|offset").encode("utf-8", errors="ignore")).digest()
    start_offset = (int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)) * usable_start
    fade_out_start = max(0.0, video_duration - 0.8)
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-ss", f"{start_offset:.3f}", "-i", music_path,
        "-filter_complex",
        (
            f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,volume={volume},"
            f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_start}:d=0.8[a]"
        ),
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        out_path,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode(errors="ignore")[-800:]
        raise RuntimeError(f"Automatic audio mix failed: {detail}")
    logger.info("Added real background music (start %.1fs)", start_offset)
    return out_path


def _to_9x16_fill(video_path, out_path):
    """Fit the entire source inside 1080x1920 without blur or cropping.

    The original frame is preserved completely and centered on a clean black
    9:16 canvas when its aspect ratio does not already match vertical video.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg not found; skipping 9:16 conversion")
        return False
    filter_complex = (
        f"[0:v]scale={REELS_WIDTH}:{REELS_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={REELS_WIDTH}:{REELS_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[v]"
    )
    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-shortest", out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Fitted full video into 9:16 frame ({REELS_WIDTH}x{REELS_HEIGHT}) without crop or blur")
        return True
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode(errors="ignore")[:500]
        logger.error(f"9:16 conversion failed ({detail}); uploading current processed video")
        return False


def prepare_video(media_url, fill_9x16=False, selection_key=""):
    """Return (name, bytes, content_type) for a video upload.

    Production rule: silent/muted videos MUST receive audio automatically.
    Audible videos preserve their original soundtrack. Priority for silent
    videos is BACKGROUND_MUSIC_URLS (licensed tracks), then
    BACKGROUND_MUSIC_PATH (file/directory), then six pinned CC0 music tracks.
    """
    auto_audio = _env_true("AUTO_ADD_AUDIO", "true")
    suffix = Path(media_url.split("?")[0]).suffix or ".mp4"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    temp_paths = [tmp_path]
    try:
        resp = requests.get(media_url, timeout=180, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(resp.content)

        name = media_url.split("?")[0].rsplit("/", 1)[-1] or "video.mp4"
        current = tmp_path

        state = audio_state(tmp_path)
        logger.info(f"Video audio state: {state}")
        if auto_audio and state in ("missing", "silent"):
            music_key = selection_key or media_url
            music_path = _resolve_music(music_key, temp_paths)
            mixed_path = tmp_path + ".mixed.mp4"
            temp_paths.append(mixed_path)
            current = _mix_music(tmp_path, music_path, mixed_path, music_key)
            verify = audio_state(current)
            if verify in ("missing", "silent"):
                raise RuntimeError("Automatic audio was added but output is still silent; refusing upload")
        elif state == "audible":
            logger.info("Video already has usable audio; preserving original sound")
        elif auto_audio and state == "unknown":
            raise RuntimeError("Could not verify video audio; refusing silent-risk upload")

        if fill_9x16:
            out_path = current + ".9x16.mp4"
            temp_paths.append(out_path)
            if _to_9x16_fill(current, out_path):
                current = out_path

        content = Path(current).read_bytes()
        return name, content, "video/mp4"
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def media_kind(media_url):
    lower = (media_url.split("?")[0] or "").lower()
    if lower.endswith(_VIDEO_EXTS):
        return "video"
    return "image"


def _matches_magic(header, kind):
    if kind == "video":
        if len(header) >= 8 and header[4:8] == b"ftyp":
            return True
        return any(header.startswith(m) for m, _ in _VIDEO_MAGIC)
    return any(header.startswith(m) for m, _ in _IMAGE_MAGIC)


def _probe_video(media_url):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return ""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=format_name,duration", "-of", "csv=p=0", media_url],
            check=False, capture_output=True, text=True, timeout=300,
        )
        if out.returncode != 0:
            detail = (out.stderr or "").strip().splitlines()
            return (detail[-1] if detail else "ffprobe rejected the file")[:300]
        if "N/A" in out.stdout or not out.stdout.strip():
            return "ffprobe could not read a valid stream"
        return ""
    except subprocess.TimeoutExpired:
        return "ffprobe timed out"
    except Exception as exc:
        return f"ffprobe error: {exc}"


def validate_media_url(media_url, kind=None, ffprobe=True):
    kind = kind or media_kind(media_url)
    if not kind:
        return "cannot determine media kind"

    resp = requests.get(
        media_url, timeout=180,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-65535"},
    )
    try:
        resp.raise_for_status()
    except Exception:
        return f"HTTP {resp.status_code} for {media_url}"

    content_type = (resp.headers.get("Content-Type", "") or "").lower().split(";")[0]
    expected = _VIDEO_TYPES if kind == "video" else _IMAGE_TYPES
    if content_type and content_type != "application/octet-stream" and content_type not in expected:
        return f"unexpected Content-Type '{content_type}' (expected {kind})"

    if not _matches_magic(resp.content[:16], kind):
        return f"magic bytes do not match {kind} media"

    if kind == "video" and ffprobe:
        return _probe_video(media_url)
    return ""
