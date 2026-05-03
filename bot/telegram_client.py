"""
telegram_client.py — Telethon MTProto client for downloading large Telegram files.

The Telegram Bot API has a hard 20 MB download limit.  By using the MTProto
protocol directly (via Telethon), we can download files up to 2 GB — the
platform maximum for standard accounts.

The client runs in *bot mode* (authenticates with the same bot token used by
aiogram) and shares the asyncio event loop.  It is used exclusively as a
download engine; all routing and UI stay in aiogram.
"""

import asyncio
import logging
import os

from telethon import TelegramClient

logger = logging.getLogger(__name__)

# Module-level singleton — initialized once in main.py
_client: TelegramClient | None = None
_download_semaphore: asyncio.Semaphore | None = None


async def init_client(api_id: int, api_hash: str, bot_token: str) -> TelegramClient:
    """Start the Telethon client in bot mode and cache it."""
    global _client, _download_semaphore
    if _client is not None:
        return _client

    _download_semaphore = asyncio.Semaphore(2)  # Limit concurrent downloads to prevent stalls

    # Store session file in the working directory (/app inside Docker)
    _client = TelegramClient(
        "telethon_bot",  # creates telethon_bot.session in CWD
        api_id,
        api_hash,
    )
    await _client.start(bot_token=bot_token)
    logger.info("Telethon MTProto client started (bot mode)")
    return _client


async def stop_client() -> None:
    """Disconnect the Telethon client gracefully."""
    global _client
    if _client is not None:
        await _client.disconnect()
        logger.info("Telethon client disconnected")
        _client = None


def get_client() -> TelegramClient:
    """Return the cached client.  Raises RuntimeError if not initialized."""
    if _client is None:
        raise RuntimeError(
            "Telethon client not initialized — "
            "set TELEGRAM_API_ID and TELEGRAM_API_HASH in your .env"
        )
    return _client


def is_available() -> bool:
    """Return True if the Telethon client has been initialized."""
    return _client is not None


async def download_file(
    chat_id: int,
    message_id: int,
    destination: str,
    progress_callback=None,
) -> str:
    """
    Download a file from a Telegram message via MTProto (no 20 MB limit).

    Args:
        chat_id:           The chat where the message was sent.
        message_id:        The ID of the message containing the file.
        destination:       Local file path to save the downloaded file.
        progress_callback: Optional callable(current_bytes, total_bytes).

    Returns:
        The path to the downloaded file (may differ from *destination*
        if Telethon appends an extension).
    """
    client = get_client()

    # Fetch the message via MTProto
    message = await client.get_messages(chat_id, ids=message_id)
    if not message or not message.media:
        raise ValueError("Message not found or contains no media")

    # Ensure the destination directory exists
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    # Download the file, limited by semaphore to prevent hanging on multiple files
    global _download_semaphore
    sem = _download_semaphore or asyncio.Semaphore(2)
    
    async with sem:
        path = await client.download_media(
            message,
            file=destination,
            progress_callback=progress_callback,
        )

    if path is None:
        raise RuntimeError("Telethon download_media returned None")

    return str(path)
