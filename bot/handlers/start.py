from aiogram.filters import Command
from aiogram.types import Message

from ..utils import is_allowed
from . import router


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Reply with welcome message and supported formats."""
    if not is_allowed(message.from_user.id):
        return  # Silent rejection

    await message.answer(
        "👋 <b>YouTube → Google Drive Bot</b>\n\n"
        "Send me a YouTube link and I'll download it and upload it to Google Drive.\n"
        "Send /dl [link] to download an arbitrary direct link, securely encrypt it with AES-256, and upload to Google Drive.\n"
        "Send /udl [link] to download an arbitrary direct link without encryption, and upload to Google Drive.\n"
        "Send /lookup_pod [query] to search for podcasts.\n"
        "Send /pod [rss-link] to get the latest episodes of a podcast.\n\n"
        "📎 <b>Send any file</b> (document, photo, video, audio) and I'll upload it directly to Google Drive.\n\n"
        "<b>Supported formats:</b> 1440p · 1080p · 720p · 480p · MP3 Audio",
        parse_mode="HTML",
    )

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Reply with a list of available commands and their descriptions."""
    if not is_allowed(message.from_user.id):
        return

    await message.answer(
        "🛠 <b>Available Commands</b>\n\n"
        "• /start - Displays welcome message\n"
        "• /help - Displays this help message\n"
        "• /dl [link] - Download, encrypt with AES-256 ZIP, and upload to Google Drive\n"
        "• /udl [link] - Download without encryption and upload to Google Drive\n"
        "• /lookup_pod [query] - Search iTunes for podcasts\n"
        "• /pod [rss-link] - Fetch latest 5 episodes from a podcast RSS feed\n\n"
        "📎 <b>Send any file</b> directly to upload it to Google Drive.\n\n"
        "<i>Just send a YouTube link to download a video/audio track directly to Google Drive!</i>",
        parse_mode="HTML"
    )
