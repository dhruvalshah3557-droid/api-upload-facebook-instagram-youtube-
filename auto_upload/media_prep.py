import logging
import os
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


def _env(key, default=""):
    value = os.getenv(key)
    return value.strip() if value is not None and value.strip() else default


def _download_bytes(media_url):
    """Download the original file bytes."""
    resp = requests.get(
        media_url,
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    name = media_url.split("?")[0].rsplit("/", 1)[-1] or "media"
    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    return name, resp.content, content_type


def _has_audio(video_path):
    """Return True/False if the file has an audio stream, None if unknown."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
            check=False, capture_output=True, text=True,
        )
        return bool(out.stdout.strip())
    except Exception:
        return None


def _mix_music(video_path, music_path, has_audio, volume=0.15):
    """Mix a low-volume instrumental track into a silent video."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg not found; uploading original (audio unchanged)")
        return video_path
    out_path = video_path + ".mixed.mp4"
    if has_audio:
        filter_complex = (
            f"[1:a]volume={volume}[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
    else:
        filter_complex = f"[1:a]volume={volume}[a]"
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", music_path,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info("Mixed low-volume background music into video")
        return out_path
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode(errors="ignore")[:500]
        logger.error(f"Background music mix failed ({detail}); uploading original")
        return video_path


REELS_WIDTH, REELS_HEIGHT = 1080, 1920


def _to_9x16_fill(video_path, out_path):
    """Re-encode a video to fill a 1080x1920 (9:16) frame.

    The video is scaled to COVER the frame and center-cropped, so the full
    frame shows the video only - no empty bars and no padded background.
    Instagram Reels and YouTube Shorts are both 9:16. Returns True on success.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg not found; skipping 9:16 conversion")
        return False
    filter_complex = (
        f"[0:v]scale={REELS_WIDTH}:{REELS_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={REELS_WIDTH}:{REELS_HEIGHT}:(in_w-out_w)/2:(in_h-out_h)/2[v]"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-shortest",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Converted video to fill 9:16 frame ({REELS_WIDTH}x{REELS_HEIGHT})")
        return True
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode(errors="ignore")[:500]
        logger.error(f"9:16 conversion failed ({detail}); uploading original")
        return False


def prepare_video(media_url, fill_9x16=False):
    """Return (name, bytes, content_type) for a video upload.

    Original sound is preserved by default (no processing). When
    MIX_BACKGROUND_MUSIC=true and the video has no audio stream, an approved
    low-volume instrumental track (BACKGROUND_MUSIC_PATH) is mixed in. When
    fill_9x16=True, the video is re-encoded to fill 1080x1920 (9:16) so only
    the video is visible (no bars). Falls back to the original file when
    ffmpeg or the track is unavailable.
    """
    mix = _env("MIX_BACKGROUND_MUSIC", "false").lower() in ("true", "1", "yes")
    music_path = _env("BACKGROUND_MUSIC_PATH", "")
    needs_processing = (mix and music_path) or fill_9x16
    if not needs_processing:
        return _download_bytes(media_url)

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

        if mix and music_path:
            has_audio = _has_audio(tmp_path)
            if has_audio is False:
                mixed = _mix_music(tmp_path, music_path, has_audio=False)
                if mixed != tmp_path:
                    current = mixed
                    temp_paths.append(mixed)
            else:
                logger.info("Video already has audio; preserving original sound")

        if fill_9x16:
            out_path = current + ".9x16.mp4"
            if _to_9x16_fill(current, out_path):
                temp_paths.append(out_path)
                content = Path(out_path).read_bytes()
                return name, content, "video/mp4"

        if current != tmp_path:
            content = Path(current).read_bytes()
            return name, content, "video/mp4"
        return name, resp.content, resp.headers.get("Content-Type", "video/mp4")
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def media_kind(media_url):
    """Return 'image' or 'video' from the URL extension ('' if unknown)."""
    lower = (media_url.split("?")[0] or "").lower()
    if lower.endswith(_VIDEO_EXTS):
        return "video"
    return "image"


def _matches_magic(header, kind):
    """Check magic bytes of the first bytes for the expected kind."""
    if kind == "video":
        # MP4/MOV families all start with an ftyp box at offset 4.
        if len(header) >= 8 and header[4:8] == b"ftyp":
            return True
        return any(header.startswith(m) for m, _ in _VIDEO_MAGIC)
    return any(header.startswith(m) for m, _ in _IMAGE_MAGIC)


def _probe_video(media_url):
    """Return a validation detail string if ffprobe rejects the video."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return ""
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=format_name,duration",
                "-of", "csv=p=0",
                media_url,
            ],
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
    """Validate a media URL before handing it to a platform API.

    Checks in order: HTTP status, Content-Type, magic bytes, and (for videos,
    when ffprobe is available) a real probe. Returns an empty string when the
    media is usable, otherwise a human-readable reason. Downloads only the
    first bytes so broken/truncated files are caught cheaply.
    """
    kind = kind or media_kind(media_url)
    if not kind:
        return "cannot determine media kind"

    resp = requests.get(
        media_url,
        timeout=180,
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
