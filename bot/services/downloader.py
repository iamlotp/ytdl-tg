"""
downloader.py — Shared HTTP file download logic.
"""
import os
import re
import time
import urllib.parse
import uuid

import aiofiles
import aiohttp

from ..config import DOWNLOAD_DIR
from ..utils import format_size


async def download_url(
    url: str,
    progress_state,
    prefix: str = "dl",
) -> tuple[str, str]:
    """Download a URL to a local file with progress tracking.

    Args:
        url:             Direct download URL.
        progress_state:  ProgressState instance to update.
        prefix:          Filename prefix for the temp file.

    Returns:
        (local_path, original_filename) tuple.
    """
    # Extract and sanitize filename from URL
    parsed_url = urllib.parse.urlparse(url)
    original_filename = os.path.basename(parsed_url.path)
    if not original_filename:
        original_filename = "downloaded_file.bin"
    original_filename = urllib.parse.unquote(original_filename)
    original_filename = re.sub(r'[\\/*?:"<>|]', "-", original_filename)

    unique_id = uuid.uuid4().hex[:8]
    local_path = os.path.join(DOWNLOAD_DIR, f"{prefix}_{unique_id}_{original_filename}")

    # Download with progress
    progress_state.action = "⬇️ Downloading file..."
    progress_state.percentage = 0.0

    timeout = aiohttp.ClientTimeout(total=0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            start_time = time.time()

            async with aiofiles.open(local_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(65536):
                    await f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        progress_state.percentage = (downloaded / total_size) * 100

                    elapsed = time.time() - start_time
                    if elapsed > 0.5:
                        speed_bps = downloaded / elapsed
                        progress_state.speed = format_size(speed_bps) + "/s"
                        if total_size > 0:
                            eta_seconds = (total_size - downloaded) / speed_bps
                            progress_state.eta = f"{int(eta_seconds)}s"

    # Fallback if Content-Length was missing
    if progress_state.percentage == 0.0 and downloaded > 0:
        progress_state.percentage = 100.0
        progress_state.speed = format_size(downloaded) + " total"

    return local_path, original_filename
