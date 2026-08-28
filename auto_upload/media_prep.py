import logging
import math
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


def _generate_trend_style_music(video_path, out_path):
    """Generate an upbeat, modern instrumental bed when no licensed track is configured.

    This is original synthetic audio, not a copyrighted platform song. It is
    deliberately rhythmic so silent product videos never publish mute.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to add audio to silent video")

    duration = _probe_duration(video_path)
    fade_out_start = max(0.0, duration - 0.6)
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={duration}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.08:sample_rate=44100:duration={duration}",
        "-filter_complex",
        (
            "[0:a]volume=0.32,tremolo=f=2:d=0.88[bass];"
            "[1:a]volume=0.10,tremolo=f=4:d=0.65[bright];"
            "[2:a]lowpass=f=1200,highpass=f=120,volume=0.035[tex];"
            f"[bass][bright][tex]amix=inputs=3:normalize=0,"
            f"afade=t=in:st=0:d=0.25,afade=t=out:st={fade_out_start}:d=0.6[a]"
        ),
        "-map", "[a]", "-c:a", "aac", "-b:a", "192k", out_path,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode(errors="ignore")[-800:]
        raise RuntimeError(f"Could not generate automatic background audio: {detail}")
    logger.info("Generated original trend-style instrumental for silent video")
    return out_path


def _resolve_music(video_path, temp_paths):
    """Resolve the music source in priority order: URL, local path, generated."""
    audio_url = _env("TRENDING_AUDIO_URL", "") or _env("BACKGROUND_MUSIC_URL", "")
    local_path = _env("BACKGROUND_MUSIC_PATH", "")

    if audio_url:
        fd, path = tempfile.mkstemp(suffix=".music")
        os.close(fd)
        temp_paths.append(path)
        _download_music_url(audio_url, path)
        logger.info("Using configured licensed/trending audio URL for silent video")
        return path

    if local_path and Path(local_path).is_file():
        logger.info("Using configured local background music for silent video")
        return local_path

    fd, path = tempfile.mkstemp(suffix=".m4a")
    os.close(fd)
    temp_paths.append(path)
    return _generate_trend_style_music(video_path, path)


def _mix_music(video_path, music_path, out_path, volume=0.34):
    """Attach background music to a silent/muted video."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to add audio to silent video")
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex", f"[1:a]volume={volume}[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        out_path,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode(errors="ignore")[-800:]
        raise RuntimeError(f"Automatic audio mix failed: {detail}")
    logger.info("Added background audio to silent video")
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


def prepare_video(media_url, fill_9x16=False):
    """Return (name, bytes, content_type) for a video upload.

    Production rule: silent/muted videos MUST receive audio automatically.
    Audible videos preserve their original soundtrack. Priority for silent
    videos is TRENDING_AUDIO_URL/BACKGROUND_MUSIC_URL (a licensed track), then
    BACKGROUND_MUSIC_PATH, then an original generated trend-style instrumental.
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
            music_path = _resolve_music(tmp_path, temp_paths)
            mixed_path = tmp_path + ".mixed.mp4"
            temp_paths.append(mixed_path)
            current = _mix_music(tmp_path, music_path, mixed_path)
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
