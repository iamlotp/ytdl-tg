"""
youtube.py — yt-dlp wrapper for metadata extraction and downloading.
"""
import logging
import os
from typing import Any, Optional

import yt_dlp

from .config import COOKIES_PATH, DOWNLOAD_DIR
from .utils import format_size

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality definitions
# Each entry maps a user-facing key → target video height (None = audio only)
# ---------------------------------------------------------------------------
QUALITY_TARGETS: list[dict] = [
    {"key": "1440p", "label": "1440p (2K)", "height": 1440},
    {"key": "1080p", "label": "1080p (FHD)", "height": 1080},
    {"key": "720p",  "label": "720p (HD)",   "height": 720},
    {"key": "480p",  "label": "480p",         "height": 480},
    {"key": "mp3",   "label": "MP3 Audio",    "height": None},
]


class YouTubeError(Exception):
    """Raised for expected yt-dlp failures (age-gate, geo-block, etc.)."""


# Mimic a real browser to avoid YouTube's bot-detection.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _ydl_opts_base() -> dict:
    """Common yt-dlp options shared between extraction and download."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        # Send a realistic browser User-Agent.
        "http_headers": {"User-Agent": _BROWSER_UA},
        # Disable ANSI color formatting to keep progress hook strings clean.
        "color": "never",
        # Enable Node.js for solving YouTube's "n parameter" JS challenge.
        # Node is installed in the container but yt-dlp only auto-enables
        # Deno; other runtimes must be opted-in explicitly.
        "js_runtimes": {"node": {}},
    }

    if os.path.isfile(COOKIES_PATH):
        log.info("Loading cookies from %s", COOKIES_PATH)
        opts["cookiefile"] = COOKIES_PATH
    else:
        log.warning(
            "Cookie file not found at %s — requests will be unauthenticated.",
            COOKIES_PATH,
        )

    return opts


def extract_info(url: str) -> dict:
    """
    Extract video metadata without downloading.

    Returns the raw yt-dlp info dict.
    Raises YouTubeError on DownloadError (age-gate, geo-block, private, etc.).
    """
    opts = _ydl_opts_base()
    opts["skip_download"] = True
    # Do NOT set 'format' here — we only want the raw info dict with all
    # available formats so our own get_quality_options() can inspect them.

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        # Always log the raw message so it appears in `docker compose logs`.
        msg = str(exc)
        log.error("yt-dlp extract_info failed for %s: %s", url, msg)
        msg_lower = msg.lower()

        # Bot / sign-in detection — most common failure for server IPs.
        if "sign in" in msg_lower or "confirm you" in msg_lower or "bot" in msg_lower:
            raise YouTubeError(
                "❌ YouTube is detecting the bot. "
                "Please refresh your cookies.txt and restart the container."
            )
        if "age" in msg_lower:
            raise YouTubeError("❌ This video is age-restricted and cannot be downloaded.")
        if "private" in msg_lower:
            raise YouTubeError("❌ This video is private.")
        if "not available" in msg_lower or "unavailable" in msg_lower:
            raise YouTubeError("❌ This video is unavailable in your region or has been removed.")
        if "requested format" in msg_lower:
            raise YouTubeError("❌ No downloadable format found for this video.")
        raise YouTubeError(f"❌ Could not fetch video info: {msg}")

    if info is None:
        raise YouTubeError("❌ yt-dlp returned no data for this URL.")

    return info


def _best_audio_format(formats: list[dict]) -> Optional[dict]:
    """Return the single best audio-only format (highest abr/tbr)."""
    audio_fmts = [
        f for f in formats
        if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    if not audio_fmts:
        return None
    return max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0)


def _best_video_format_for_height(
    formats: list[dict], target_height: int
) -> Optional[dict]:
    """
    Return the best video-only format whose height is <= target_height.
    Prefers the closest match from below; falls back to the nearest above.
    """
    video_fmts = [
        f for f in formats
        if f.get("vcodec") != "none" and f.get("acodec") == "none"
        and f.get("height") is not None
    ]
    if not video_fmts:
        # Fall back to combined formats
        video_fmts = [
            f for f in formats
            if f.get("vcodec") not in (None, "none")
            and f.get("height") is not None
        ]

    # Prefer formats at or below the target height
    at_or_below = [f for f in video_fmts if f["height"] <= target_height]
    if at_or_below:
        return max(at_or_below, key=lambda f: (f["height"], f.get("tbr") or 0))

    # Fall back to the lowest format above the target
    above = [f for f in video_fmts if f["height"] > target_height]
    if above:
        return min(above, key=lambda f: f["height"])

    return None


def _get_size(fmt: Optional[dict]) -> Optional[int]:
    """Return filesize or filesize_approx (in bytes), or None."""
    if fmt is None:
        return None
    return fmt.get("filesize") or fmt.get("filesize_approx")


def get_quality_options(info: dict) -> list[dict]:
    """
    Build a list of quality options with combined size estimates.

    Each entry:
    {
      "key":        str,   # e.g. "1080p"
      "label":      str,   # e.g. "1080p (FHD)"
      "format_id":  str,   # video format ID (or audio format ID for mp3)
      "size_str":   str,   # human-readable combined size
    }
    """
    formats: list[dict] = info.get("formats", [])
    best_audio = _best_audio_format(formats)
    audio_size = _get_size(best_audio)

    options: list[dict] = []

    for target in QUALITY_TARGETS:
        if target["height"] is None:
            # MP3 — audio only
            if best_audio is None:
                continue
            size = audio_size
            options.append({
                "key":       target["key"],
                "label":     target["label"],
                "format_id": best_audio["format_id"],
                "size_str":  format_size(size),
            })
        else:
            vid_fmt = _best_video_format_for_height(formats, target["height"])
            if vid_fmt is None:
                continue  # Quality not available — skip this button
            vid_size = _get_size(vid_fmt)

            # Combined size: video + audio
            if vid_size is not None and audio_size is not None:
                combined: Optional[int] = vid_size + audio_size
            elif vid_size is not None:
                combined = vid_size
            else:
                combined = None

            options.append({
                "key":       target["key"],
                "label":     target["label"],
                "format_id": vid_fmt["format_id"],
                "size_str":  format_size(combined),
            })

    return options


def download(video_id: str, format_key: str, output_path: str, progress_hook=None) -> None:
    """
    Download the video/audio identified by *format_key* for *video_id*,
    writing the merged result to *output_path*.

    If *progress_hook* is provided, it is called by yt-dlp on progress events.

    This is a synchronous blocking call — run it in asyncio.to_thread().
    Raises YouTubeError on failure.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    opts = _ydl_opts_base()
    opts["outtmpl"] = output_path

    url = f"https://www.youtube.com/watch?v={video_id}"

    if format_key == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # Map quality key → approximate height for format selection
        height_map = {"1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
        height = height_map.get(format_key, 1080)
        # Fallback chain: split mp4/m4a → split any → any split → combined
        opts["format"] = (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={height}]+bestaudio"
            f"/bestvideo+bestaudio/best"
        )
        opts["merge_output_format"] = "mp4"
        
        # Enable English subtitles (manual and auto-generated)
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["en.*", "en"]
        
        opts["postprocessors"] = [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            },
            {
                "key": "FFmpegEmbedSubtitle",
            }
        ]

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        # If the error is specifically about subtitles (like HTTP 429), retry without them.
        if "subtitle" in str(exc).lower():
            log.warning("Subtitle download failed, retrying without subtitles...")
            opts["writesubtitles"] = False
            opts["writeautomaticsub"] = False
            opts.pop("subtitleslangs", None)
            if "postprocessors" in opts:
                opts["postprocessors"] = [
                    pp for pp in opts["postprocessors"]
                    if pp.get("key") != "FFmpegEmbedSubtitle"
                ]
            try:
                with yt_dlp.YoutubeDL(opts) as ydl_fallback:
                    ydl_fallback.download([url])
            except yt_dlp.utils.DownloadError as exc_fallback:
                raise YouTubeError(f"❌ Download failed: {exc_fallback}") from exc_fallback
        else:
            raise YouTubeError(f"❌ Download failed: {exc}") from exc
