import re
import uuid
from typing import Optional

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


def extract_video_id(text: str) -> Optional[str]:
    """Return the 11-char YouTube video ID from *text*, or None."""
    match = YOUTUBE_URL_REGEX.search(text)
    return match.group("video_id") if match else None


def format_size(size_bytes: Optional[int]) -> str:
    """Convert a byte count into a human-readable string (e.g. '1.3 GB')."""
    if size_bytes is None:
        return "Size Unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} TB"


def generate_unique_filename(video_id: str, ext: str) -> str:
    """
    Return a filename that is unique per invocation to prevent file collisions
    when the same video is downloaded concurrently by multiple users.
    Format:  <video_id>_<uuid4>.<ext>
    """
    return f"{video_id}_{uuid.uuid4().hex}.{ext}"
