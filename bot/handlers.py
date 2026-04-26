"""
handlers.py — Telegram message and callback query handlers.
"""
import asyncio
import logging
import os
import re
import math
import uuid
import secrets
import urllib.parse
import aiohttp
import aiofiles
import pyzipper
import feedparser

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import time


from . import drive, youtube
from .config import DOWNLOAD_DIR, WHITELIST_IDS
from .utils import YOUTUBE_URL_REGEX, extract_video_id, generate_unique_filename

logger = logging.getLogger(__name__)
router = Router()

class ProgressState:
    def __init__(self):
        self.action = "Starting..."
        self.percentage = 0.0
        self.speed = ""
        self.eta = ""
        self.done = False

async def progress_updater(msg: Message, state: ProgressState):
    """Periodically edit message with current progress."""
    last_text = ""
    while not state.done:
        bar_len = 20
        filled_len = int(bar_len * state.percentage // 100)
        bar = "█" * filled_len + "░" * (bar_len - filled_len)

        text = f"<b>{state.action}</b>\n"
        text += f"<code>[{bar}] {state.percentage:.1f}%</code>\n"
        
        details = []
        if state.speed:
            details.append(f"Speed: {state.speed}")
        if state.eta:
            details.append(f"ETA: {state.eta}")
            
        if details:
            text += " · ".join(details)

        if text != last_text:
            await _safe_edit_caption_or_text(msg, text, parse_mode="HTML")
            last_text = text
            
        await asyncio.sleep(2.5)  # Telegram limits message edits



# ---------------------------------------------------------------------------
# Whitelist guard
# ---------------------------------------------------------------------------
def _is_allowed(user_id: int) -> bool:
    """Return True if the user is on the whitelist (or whitelist is empty)."""
    if not WHITELIST_IDS:
        return True  # No whitelist configured → allow everyone
    return user_id in WHITELIST_IDS


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
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


# ---------------------------------------------------------------------------
# /help command
# ---------------------------------------------------------------------------
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
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


# ---------------------------------------------------------------------------
# /dl command (direct download & encrypt)
# ---------------------------------------------------------------------------
@router.message(Command("dl"))
async def cmd_dl(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /dl [direct_link]\nExample: <code>/dl https://example.com/file.mp4</code>", parse_mode="HTML")
        return

    url = parts[1].strip()

    status_msg = await message.answer("⏳ <b>Initializing download…</b>", parse_mode="HTML")
    
    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    local_path = None
    zip_path = None

    try:
        # Extract original filename
        parsed_url = urllib.parse.urlparse(url)
        original_filename = os.path.basename(parsed_url.path)
        if not original_filename:
            original_filename = "downloaded_file.bin"
        original_filename = urllib.parse.unquote(original_filename)
        
        # Clean original filename
        original_filename = re.sub(r'[\\/*?:"<>|]', "-", original_filename)
        
        unique_id = uuid.uuid4().hex[:8]
        local_path = os.path.join(DOWNLOAD_DIR, f"dl_{unique_id}_{original_filename}")
        zip_path = f"{local_path}.zip"

        # 1. Download
        state.action = "⬇️ Downloading file..."
        state.percentage = 0.0

        async with aiohttp.ClientSession() as session:
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
                            state.percentage = (downloaded / total_size) * 100
                            
                        elapsed = time.time() - start_time
                        if elapsed > 0.5:
                            speed_bps = downloaded / elapsed
                            state.speed = _format_size(speed_bps) + "/s"
                            if total_size > 0:
                                eta_seconds = (total_size - downloaded) / speed_bps
                                state.eta = f"{int(eta_seconds)}s"
        
        # Provide fallback percentage if Content-Length was missing but download finished
        if state.percentage == 0.0 and downloaded > 0:
            state.percentage = 100.0
            state.speed = _format_size(downloaded) + " total"

        # 2. Encrypt — streaming AES-256 ZIP (64 KB chunks, constant RAM usage)
        state.action = "🔒 Encrypting..."
        state.percentage = 0.0
        state.speed = ""
        state.eta = ""
        
        password = secrets.token_urlsafe(12)
        
        def zip_and_encrypt_streaming():
            """Write an AES-256 encrypted ZIP in 64 KB chunks via zf.open().

            - Uses pyzipper's streaming write API (never loads full file into RAM)
            - Manual chunk loop so we can report progress to state.percentage
            - Manual try/finally close to absorb pyzipper's spurious
              'open writing handle' ValueError on close
            """
            file_size = os.path.getsize(local_path)
            encrypted = 0
            CHUNK = 65536  # 64 KB

            zf = pyzipper.AESZipFile(
                zip_path, 'w',
                compression=pyzipper.ZIP_STORED,
                encryption=pyzipper.WZ_AES,
                allowZip64=True,
            )
            try:
                zf.setpassword(password.encode('utf-8'))
                # Use a generic internal name — ZIP stores filenames in plaintext
                # in its central directory, visible without the password.
                # Storing "file" (+ extension) hides the real name from anyone
                # who inspects the archive without knowing the password.
                internal_name = "file" + os.path.splitext(original_filename)[1]
                with zf.open(internal_name, 'w', force_zip64=True) as dest:
                    with open(local_path, 'rb') as src:
                        while True:
                            chunk = src.read(CHUNK)
                            if not chunk:
                                break
                            dest.write(chunk)
                            encrypted += len(chunk)
                            if file_size > 0:
                                state.percentage = (encrypted / file_size) * 100
            finally:
                try:
                    zf.close()
                except ValueError as ve:
                    if "open writing handle" not in str(ve):
                        raise

        await asyncio.to_thread(zip_and_encrypt_streaming)

        # 3. Upload to Google Drive
        def drive_progress_hook(progress: float):
            state.action = "☁️ Uploading to Google Drive..."
            state.percentage = progress * 100
            state.speed = ""
            state.eta = ""

        drive_filename = f"{original_filename}.enc.zip"

        result = await asyncio.to_thread(
            drive.upload, zip_path, drive_filename, drive_progress_hook
        )
        
        state.done = True
        updater_task.cancel()

        # 4. Deliver
        view_link   = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>\n\n"
            "🔑 <b>Password to extract:</b>\n"
            f"<code>{password}</code>\n\n"
            "📄 <b>Original filename:</b>\n"
            f"<code>{_escape_html(original_filename)}</code>\n\n"
            "<i>Open the .zip with 7-Zip or WinRAR, enter the password above,\n"
            "then rename the extracted file to the original filename.\n"
            "The filename inside the archive is intentionally generic to keep it private.</i>"
        )
        
        await _safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("DL command failed")
        await _safe_edit_caption_or_text(
            status_msg, f"❌ Error: {_escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()
        
        # Cleanup
        for path in [local_path, zip_path]:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                    logger.debug("Cleaned up %s", path)
                except OSError as exc:
                    logger.warning("Failed to delete temp file %s: %s", path, exc)


# ---------------------------------------------------------------------------
# /udl command (un-encrypted direct download)
# ---------------------------------------------------------------------------
@router.message(Command("udl"))
async def cmd_udl(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /udl [direct_link]\nExample: <code>/udl https://example.com/file.mp4</code>", parse_mode="HTML")
        return

    url = parts[1].strip()

    status_msg = await message.answer("⏳ <b>Initializing download…</b>", parse_mode="HTML")
    
    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    local_path = None

    try:
        # Extract original filename
        parsed_url = urllib.parse.urlparse(url)
        original_filename = os.path.basename(parsed_url.path)
        if not original_filename:
            original_filename = "downloaded_file.bin"
        original_filename = urllib.parse.unquote(original_filename)
        
        # Clean original filename
        original_filename = re.sub(r'[\\/*?:"<>|]', "-", original_filename)
        
        unique_id = uuid.uuid4().hex[:8]
        local_path = os.path.join(DOWNLOAD_DIR, f"udl_{unique_id}_{original_filename}")

        # 1. Download
        state.action = "⬇️ Downloading file..."
        state.percentage = 0.0

        async with aiohttp.ClientSession() as session:
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
                            state.percentage = (downloaded / total_size) * 100
                            
                        elapsed = time.time() - start_time
                        if elapsed > 0.5:
                            speed_bps = downloaded / elapsed
                            state.speed = _format_size(speed_bps) + "/s"
                            if total_size > 0:
                                eta_seconds = (total_size - downloaded) / speed_bps
                                state.eta = f"{int(eta_seconds)}s"
        
        # Provide fallback percentage if Content-Length was missing but download finished
        if state.percentage == 0.0 and downloaded > 0:
            state.percentage = 100.0
            state.speed = _format_size(downloaded) + " total"

        # 2. Upload to Google Drive
        def drive_progress_hook(progress: float):
            state.action = "☁️ Uploading to Google Drive..."
            state.percentage = progress * 100
            state.speed = ""
            state.eta = ""

        drive_filename = original_filename

        result = await asyncio.to_thread(
            drive.upload, local_path, drive_filename, drive_progress_hook
        )
        
        state.done = True
        updater_task.cancel()

        # 3. Deliver
        view_link   = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
        )
        
        await _safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("UDL command failed")
        await _safe_edit_caption_or_text(
            status_msg, f"❌ Error: {_escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()
        
        # Cleanup
        if local_path and os.path.isfile(local_path):
            try:
                os.remove(local_path)
                logger.debug("Cleaned up %s", local_path)
            except OSError as exc:
                logger.warning("Failed to delete temp file %s: %s", local_path, exc)


# ---------------------------------------------------------------------------
# /lookup_pod command
# ---------------------------------------------------------------------------
@router.message(Command("lookup_pod"))
async def cmd_lookup_pod(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /lookup_pod [query]\nExample: <code>/lookup_pod lex fridman</code>", parse_mode="HTML")
        return

    query = parts[1].strip()
    status_msg = await message.answer("🔍 <b>Searching for podcasts…</b>", parse_mode="HTML")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(query)}&limit=5"
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                
        results = data.get("results", [])
        if not results:
            await _safe_edit_caption_or_text(status_msg, "❌ No podcasts found.", parse_mode="HTML")
            return
            
        text = f"🎙 <b>Top 5 results for '{_escape_html(query)}'</b>:\n\n"
        for i, res in enumerate(results, start=1):
            name = _escape_html(res.get("collectionName", "Unknown Title"))
            author = _escape_html(res.get("artistName", "Unknown Author"))
            feed = _escape_html(res.get("feedUrl", ""))
            
            text += f"{i}. <b>{name}</b> by {author}\n"
            text += f"Feed: <code>{feed}</code>\n\n"
            
        await _safe_edit_caption_or_text(status_msg, text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("lookup_pod failed")
        await _safe_edit_caption_or_text(status_msg, f"❌ Error: {_escape_html(str(exc))}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /pod command
# ---------------------------------------------------------------------------
@router.message(Command("pod"))
async def cmd_pod(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /pod [rss-link]\nExample: <code>/pod https://example.com/feed.xml</code>", parse_mode="HTML")
        return

    rss_url = parts[1].strip()
    status_msg = await message.answer("⏳ <b>Fetching podcast feed…</b>", parse_mode="HTML")

    try:
        def fetch_feed(url):
            return feedparser.parse(url)
            
        feed = await asyncio.to_thread(fetch_feed, rss_url)
        
        if getattr(feed, 'bozo', 0) == 1 and not feed.entries:
            # Maybe invalid feed
            await _safe_edit_caption_or_text(status_msg, "❌ Could not parse the RSS feed.", parse_mode="HTML")
            return
            
        entries = feed.entries[:5]
        if not entries:
            await _safe_edit_caption_or_text(status_msg, "❌ No episodes found in the feed.", parse_mode="HTML")
            return

        text = f"🎧 <b>Last 5 episodes of {_escape_html(feed.feed.get('title', 'Unknown Podcast'))}</b>:\n\n"
        
        for i, entry in enumerate(entries, start=1):
            title = _escape_html(entry.get("title", "Unknown Episode"))
            
            # Find the audio enclosure
            download_link = ""
            for link in entry.get("links", []):
                if link.get("rel") == "enclosure" and link.get("type", "").startswith("audio/"):
                    download_link = link.get("href")
                    break
                    
            if not download_link and "link" in entry:
                download_link = entry.link
                
            # Truncate description and add collapsible block (blockquote)
            desc = entry.get("summary", "")
            # strip html tags
            desc = re.sub(r'<[^>]+>', '', desc)
            if len(desc) > 300:
                desc = desc[:300] + "..."
            desc = _escape_html(desc)
            
            text += f"{i}. <b>{title}</b>\n"
            text += f"<blockquote expandable>{desc}</blockquote>\n"
            if download_link:
                text += f"⬇️ <a href='{download_link}'>Download / Listen</a>\n\n"
            else:
                text += "⬇️ No audio link found\n\n"

        await _safe_edit_caption_or_text(status_msg, text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("pod failed")
        await _safe_edit_caption_or_text(status_msg, f"❌ Error: {_escape_html(str(exc))}", parse_mode="HTML")


# ---------------------------------------------------------------------------
# YouTube URL handler

# ---------------------------------------------------------------------------
@router.message(F.text.regexp(YOUTUBE_URL_REGEX))
async def handle_youtube_url(message: Message) -> None:
    # Whitelist check — silent rejection for unknown users
    if not _is_allowed(message.from_user.id):
        return

    url = message.text.strip()
    video_id = extract_video_id(url)
    if not video_id:
        return  # Regex matched but ID extraction failed — ignore

    # Send placeholder that we'll edit later
    status_msg = await message.answer("⏳ Fetching video info, please wait…")

    # --- Extract metadata (blocking) ---
    try:
        info = await asyncio.to_thread(youtube.extract_info, url)
    except youtube.YouTubeError as exc:
        await _safe_edit(status_msg, _escape_html(str(exc)), parse_mode="HTML")
        return

    # Reject live streams explicitly
    if info.get("is_live"):
        await _safe_edit(
            status_msg,
            "🔴 <b>Live streams are not supported.</b>\n"
            "Please send the link after the stream has ended.",
            parse_mode="HTML",
        )
        return

    # Build quality options
    options = youtube.get_quality_options(info)
    if not options:
        await _safe_edit(status_msg, "❌ No downloadable formats found for this video.")
        return

    # Build inline keyboard
    # Callback data format: dl|<video_id>|<quality_key>  (fits in 64 bytes)
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{opt['label']}  —  {opt['size_str']}",
                callback_data=f"dl|{video_id}|{opt['key']}",
            )
        ]
        for opt in options
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    title = info.get("title", "Unknown Title")
    thumbnail_url = info.get("thumbnail")

    # Delete the placeholder
    try:
        await status_msg.delete()
    except TelegramBadRequest:
        pass

    caption = (
        f"🎬 <b>{_escape_html(title)}</b>\n\n"
        "Select a quality to download and upload to Google Drive:"
    )

    if thumbnail_url:
        try:
            await message.answer_photo(
                photo=thumbnail_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest:
            pass  # Thumbnail fetch failed — fall back to text

    await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Callback: quality selected
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("dl|"))
async def handle_download_callback(callback: CallbackQuery) -> None:
    # Acknowledge immediately to stop the "loading" spinner on the button
    await callback.answer()

    if not _is_allowed(callback.from_user.id):
        return  # Silent

    parts = callback.data.split("|")
    if len(parts) != 3:
        await callback.answer("⚠️ Invalid selection.", show_alert=True)
        return

    _, video_id, format_key = parts

    # Initialize progress tracking
    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(callback.message, state))

    def yt_progress_hook(d: dict):
        if d['status'] == 'downloading':
            state.action = "⬇️ Downloading from YouTube..."
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total:
                state.percentage = (d.get('downloaded_bytes', 0) / total) * 100
            if '_speed_str' in d:
                state.speed = d['_speed_str'].strip()
            if '_eta_str' in d:
                state.eta = d['_eta_str'].strip()
        elif d['status'] == 'finished':
            state.action = "⚙️ Processing / Merging..."
            state.percentage = 100.0
            state.speed = ""
            state.eta = ""

    def drive_progress_hook(progress: float):
        state.action = "☁️ Uploading to Google Drive..."
        state.percentage = progress * 100
        state.speed = ""
        state.eta = ""

    # Determine file extension
    ext = "mp3" if format_key == "mp3" else "mp4"
    unique_name = generate_unique_filename(video_id, ext)
    local_path = os.path.join(DOWNLOAD_DIR, unique_name)

    try:
        # --- Download (blocking I/O in thread pool) ---
        try:
            await asyncio.to_thread(
                youtube.download, video_id, format_key, local_path, yt_progress_hook
            )
        except youtube.YouTubeError as exc:
            state.done = True
            updater_task.cancel()
            await _safe_edit_caption_or_text(callback.message, _escape_html(str(exc)), parse_mode="HTML")
            return

        # Resolve actual filename (yt-dlp may append extension)
        actual_path = _resolve_actual_path(local_path, ext)
        
        # Extract title from message
        msg_text = callback.message.caption or callback.message.text or ""
        video_title = video_id
        if msg_text.startswith("🎬"):
            # The text is plain (HTML tags are not included in .text / .caption)
            video_title = msg_text.split("\n\n")[0].replace("🎬", "").strip()
            
        # Build clean filename
        safe_title = re.sub(r'[\\/*?:"<>|]', "-", video_title)
        quality_label = format_key.upper() if format_key == "mp3" else format_key
        drive_filename = f"{safe_title} - {quality_label}.{ext}"

        # --- Upload (blocking I/O in thread pool) ---
        await _safe_edit_caption_or_text(
            callback.message, "☁️ <b>Uploading to Google Drive…</b>", parse_mode="HTML"
        )

        try:
            result = await asyncio.to_thread(
                drive.upload, actual_path, drive_filename, drive_progress_hook
            )
        except Exception as exc:
            state.done = True
            updater_task.cancel()
            logger.exception("Drive upload failed")
            await _safe_edit_caption_or_text(
                callback.message, f"❌ Upload failed: {_escape_html(str(exc))}", parse_mode="HTML"
            )
            return

        state.done = True
        updater_task.cancel()

        # --- Deliver links ---
        view_link   = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>\n\n"
            "<i>Note: Files larger than 100 MB may show a Google virus-scan "
            "warning page before downloading — this is a Google limitation.</i>"
        )

        await _safe_edit_caption_or_text(
            callback.message, final_text, parse_mode="HTML"
        )

    finally:
        state.done = True
        updater_task.cancel()
        # Always clean up local files to prevent disk bloat
        import glob
        # We need to clean up local_path, actual_path, and any other generated files like subtitles (.en.vtt, .srt)
        base_name = os.path.splitext(local_path)[0]
        cleanup_patterns = [
            local_path,
            actual_path if "actual_path" in locals() else local_path,
            f"{base_name}*.*", # catches subtitle files with the same base name
            f"{local_path}*"   # catches subtitle files appended to the local path
        ]
        
        cleaned = set()
        for pattern in cleanup_patterns:
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


# ---------------------------------------------------------------------------
# File → Google Drive handler (documents, photos, videos, audio, voice, etc.)
# ---------------------------------------------------------------------------
@router.message(F.document | F.photo | F.video | F.audio | F.voice | F.video_note)
async def handle_file_to_drive(message: Message) -> None:
    """Receive any file sent to the bot, download via MTProto, upload to Drive."""
    if not _is_allowed(message.from_user.id):
        return

    # Ensure the Telethon client is available
    from . import telegram_client
    if not telegram_client.is_available():
        await message.answer(
            "⚠️ <b>File uploads are disabled.</b>\n"
            "<i>TELEGRAM_API_ID and TELEGRAM_API_HASH are not configured.</i>",
            parse_mode="HTML",
        )
        return

    # --- Extract file metadata based on message type ---
    if message.document:
        file_name = message.document.file_name or "document"
        file_size = message.document.file_size or 0
    elif message.photo:
        # Photos arrive as a list of sizes — take the largest
        file_name = f"photo_{message.message_id}.jpg"
        file_size = message.photo[-1].file_size or 0
    elif message.video:
        file_name = message.video.file_name or f"video_{message.message_id}.mp4"
        file_size = message.video.file_size or 0
    elif message.audio:
        file_name = message.audio.file_name or f"audio_{message.message_id}.mp3"
        file_size = message.audio.file_size or 0
    elif message.voice:
        file_name = f"voice_{message.message_id}.ogg"
        file_size = message.voice.file_size or 0
    elif message.video_note:
        file_name = f"video_note_{message.message_id}.mp4"
        file_size = message.video_note.file_size or 0
    else:
        return

    # Sanitize filename
    file_name = re.sub(r'[\\/*?:"<>|]', "-", file_name)

    status_msg = await message.answer(
        f"📥 <b>Receiving file:</b> <code>{_escape_html(file_name)}</code>\n"
        f"📦 <b>Size:</b> {_format_size(file_size)}",
        parse_mode="HTML",
    )

    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    unique_id = uuid.uuid4().hex[:8]
    local_path = os.path.join(DOWNLOAD_DIR, f"tg_{unique_id}_{file_name}")
    actual_path = local_path  # Telethon may return a slightly different path

    try:
        # 1. Download from Telegram via MTProto
        state.action = "⬇️ Downloading from Telegram..."
        state.percentage = 0.0

        def tg_progress(current, total):
            if total > 0:
                state.percentage = (current / total) * 100
                state.speed = f"{_format_size(current)} / {_format_size(total)}"

        actual_path = await telegram_client.download_file(
            chat_id=message.chat.id,
            message_id=message.message_id,
            destination=local_path,
            progress_callback=tg_progress,
        )

        # 2. Upload to Google Drive (into "Telegram Uploads" subfolder)
        def drive_progress_hook(progress: float):
            state.action = "☁️ Uploading to Google Drive..."
            state.percentage = progress * 100
            state.speed = ""
            state.eta = ""

        subfolder_id = await asyncio.to_thread(
            drive.get_or_create_subfolder, "Telegram Uploads"
        )

        result = await asyncio.to_thread(
            drive.upload, actual_path, file_name, drive_progress_hook, subfolder_id
        )

        state.done = True
        updater_task.cancel()

        # 3. Reply with links
        view_link = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"📄 <b>File:</b> <code>{_escape_html(file_name)}</code>\n"
            f"📦 <b>Size:</b> {_format_size(file_size)}\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
        )

        await _safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("File-to-Drive upload failed")
        state.done = True
        updater_task.cancel()
        await _safe_edit_caption_or_text(
            status_msg, f"❌ Error: {_escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()
        # Cleanup — delete both the expected and actual paths (may differ)
        for path in {local_path, actual_path}:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                    logger.debug("Cleaned up %s", path)
                except OSError as exc:
                    logger.warning("Failed to delete temp file %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _safe_edit(msg: Message, text: str, **kwargs) -> None:
    """Edit a message, ignoring 'message not modified' errors."""
    try:
        await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit_text failed: %s", exc)


async def _safe_edit_caption_or_text(msg: Message, text: str, **kwargs) -> None:
    """Edit caption (photo messages) or text (plain messages) gracefully."""
    try:
        if msg.photo or msg.video:
            await msg.edit_caption(caption=text, **kwargs)
        else:
            await msg.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit failed: %s", exc)


def _resolve_actual_path(base_path: str, ext: str) -> str:
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


def _escape_html(text: str) -> str:
    """Minimal HTML escaping for Telegram parse_mode=HTML."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _format_size(size_bytes: float) -> str:
    """Format size in bytes to a human-readable string."""
    if size_bytes <= 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"
