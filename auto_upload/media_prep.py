import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


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


def prepare_video(media_url):
    """Return (name, bytes, content_type) for a video upload.

    Original sound is preserved by default (no processing). When
    MIX_BACKGROUND_MUSIC=true and the video has no audio stream, an approved
    low-volume instrumental track (BACKGROUND_MUSIC_PATH) is mixed in. Falls
    back to the original file when ffmpeg or the track is unavailable.
    """
    mix = _env("MIX_BACKGROUND_MUSIC", "false").lower() in ("true", "1", "yes")
    music_path = _env("BACKGROUND_MUSIC_PATH", "")
    if not mix or not music_path:
        return _download_bytes(media_url)

    suffix = Path(media_url.split("?")[0]).suffix or ".mp4"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    processed_path = ""
    try:
        resp = requests.get(media_url, timeout=180, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(resp.content)

        name = media_url.split("?")[0].rsplit("/", 1)[-1] or "video.mp4"
        has_audio = _has_audio(tmp_path)
        if has_audio is not False:
            logger.info("Video already has audio; preserving original sound")
            return name, resp.content, resp.headers.get("Content-Type", "video/mp4")

        processed_path = _mix_music(tmp_path, music_path, has_audio=False)
        content = Path(processed_path).read_bytes()
        return name, content, "video/mp4"
    finally:
        for path in (tmp_path, processed_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
