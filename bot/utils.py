import logging
import math
import os
import re
import shutil
import uuid

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from .config import WHITELIST_IDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YouTube URL regex
# Matches: youtube.com/watch, youtu.be/, youtube.com/shorts/
# ---------------------------------------------------------------------------
YOUTUBE_URL_REGEX = re.compile(
    r"(https?://)?"
    r"(www\.)?"
    r"(youtube\.com/(watch\?.*v=|shorts/)|youtu\.be/)"
    r"(?P<video_id>[A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)


def extract_video_id(text: str) -> str | None:
    """Return the 11-char YouTube video ID from *text*, or None."""
    match = YOUTUBE_URL_REGEX.search(text)
    return match.group("video_id") if match else None


def generate_unique_filename(video_id: str, ext: str) -> str:
    """
    Return a filename that is unique per invocation to prevent file collisions
    when the same video is downloaded concurrently by multiple users.
    Format:  <video_id>_<uuid4>.<ext>
    """
    return f"{video_id}_{uuid.uuid4().hex}.{ext}"


def is_allowed(user_id: int) -> bool:
    """Return True if the user is on the whitelist (or whitelist is empty)."""
    if not WHITELIST_IDS:
        return True  # No whitelist configured → allow everyone
    return user_id in WHITELIST_IDS


def escape_html(text: str) -> str:
    """Minimal HTML escaping for Telegram parse_mode=HTML."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def format_size(size_bytes: float | int | None) -> str:
    """Format size in bytes to a human-readable string."""
    if size_bytes is None:
        return "Size Unknown"
    if size_bytes <= 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    i = min(i, len(size_name) - 1)
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


def check_disk_space(path: str, required_bytes: int = 500 * 1024 * 1024) -> bool:
    """Return True if at least `required_bytes` are free at `path`."""
    usage = shutil.disk_usage(path)
    return usage.free >= required_bytes


def get_disk_free(path: str) -> str:
    """Return human-readable free disk space."""
    usage = shutil.disk_usage(path)
    return format_size(usage.free)


def cleanup_paths(*paths: str | None) -> None:
    """Delete temp files, logging success/failure. Ignores None and missing files."""
    for path in paths:
        if path and os.path.isfile(path):
            try:
                os.remove(path)
                logger.debug("Cleaned up %s", path)
            except OSError as exc:
                logger.warning("Failed to delete temp file %s: %s", path, exc)


def cleanup_glob(*patterns: str | None) -> None:
    """Delete files matching glob patterns."""
    import glob
    cleaned: set[str] = set()
    for pattern in patterns:
        if not pattern:
            continue
        for path in glob.glob(pattern):
            if path not in cleaned and os.path.isfile(path):
                try:
                    os.remove(path)
                    logger.debug("Cleaned up %s", path)
                    cleaned.add(path)
                except OSError as exc:
                    logger.warning("Failed to delete temp file %s: %s", path, exc)


def resolve_actual_path(base_path: str, ext: str) -> str:
    """
    yt-dlp sometimes appends the extension automatically.
    Return the actual file path that exists on disk.
    """
    candidates = [base_path, f"{base_path}.{ext}"]
    for path in candidates:
        if os.path.isfile(path):
            return path
    # Last resort — return base_path and let the caller handle the error
    return base_path


async def safe_edit(msg: Message, text: str, **kwargs) -> None:
    """Edit a message, ignoring 'message not modified' errors."""
    try:
        await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit_text failed: %s", exc)


async def safe_edit_caption_or_text(msg: Message, text: str, **kwargs) -> None:
    """Edit caption (photo messages) or text (plain messages) gracefully."""
    try:
        if msg.photo or msg.video:
            await msg.edit_caption(caption=text, **kwargs)
        else:
            await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit failed: %s", exc)
