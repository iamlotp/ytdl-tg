import asyncio
import glob
import logging
import os
import re
import uuid

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import youtube
from ..config import DOWNLOAD_DIR
from ..services.progress import ProgressState, progress_updater
from ..services.semaphore import download_semaphore
from ..services.uploader import upload_to_drive_with_retry
from ..utils import (
    YOUTUBE_URL_REGEX,
    check_disk_space,
    cleanup_glob,
    escape_html,
    extract_video_id,
    generate_unique_filename,
    get_disk_free,
    is_allowed,
    resolve_actual_path,
    safe_edit,
    safe_edit_caption_or_text,
)
from . import router

logger = logging.getLogger(__name__)

@router.message(F.text.regexp(YOUTUBE_URL_REGEX))
async def handle_youtube_url(message: Message) -> None:
    """Handle YouTube URLs, extract video info, and provide download options."""
    # Whitelist check — silent rejection for unknown users
    if not is_allowed(message.from_user.id):
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
        await safe_edit(status_msg, escape_html(str(exc)), parse_mode="HTML")
        return

    # Reject live streams explicitly
    if info.get("is_live"):
        await safe_edit(
            status_msg,
            "🔴 <b>Live streams are not supported.</b>\n"
            "Please send the link after the stream has ended.",
            parse_mode="HTML",
        )
        return

    # Build quality options
    options = youtube.get_quality_options(info)
    if not options:
        await safe_edit(status_msg, "❌ No downloadable formats found for this video.")
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
        f"🎬 <b>{escape_html(title)}</b>\n\n"
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


@router.callback_query(F.data.startswith("dl|"))
async def handle_download_callback(callback: CallbackQuery) -> None:
    """Handle callback to download a selected YouTube quality and upload it."""
    # Acknowledge immediately to stop the "loading" spinner on the button
    await callback.answer()

    if not is_allowed(callback.from_user.id):
        return  # Silent

    parts = callback.data.split("|")
    if len(parts) != 3:
        await callback.answer("⚠️ Invalid selection.", show_alert=True)
        return

    _, video_id, format_key = parts
    skip_cleanup = False

    if not check_disk_space(DOWNLOAD_DIR):
        await callback.message.answer(
            f"⚠️ Low disk space ({get_disk_free(DOWNLOAD_DIR)} free). "
            "Please try again later."
        )
        return

    request_id = uuid.uuid4().hex[:8]
    log = logger.getChild(f"yt.{request_id}")
    log.info("User %s started YT dl: %s (format: %s)", callback.from_user.id, video_id, format_key)

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

    if download_semaphore.locked():
        await safe_edit_caption_or_text(callback.message, "⏳ Server is busy. Your request is queued...", parse_mode="HTML")

    try:
        async with download_semaphore:
            # --- Download (blocking I/O in thread pool) ---
            try:
                await asyncio.to_thread(
                    youtube.download, video_id, format_key, local_path, yt_progress_hook
                )
            except youtube.YouTubeError:
                state.done = True
            updater_task.cancel()
            await safe_edit_caption_or_text(callback.message, escape_html(str(exc)), parse_mode="HTML")
            return

        # Resolve actual filename (yt-dlp may append extension)
        actual_path = resolve_actual_path(local_path, ext)
        log.info("Download complete: %s", actual_path)

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
        await safe_edit_caption_or_text(
            callback.message, "☁️ <b>Uploading to Google Drive…</b>", parse_mode="HTML"
        )

        base_name = os.path.splitext(local_path)[0]
        cleanup_patterns = [
            local_path, actual_path, f"{base_name}*.*", f"{local_path}*"
        ]
        paths_to_cleanup = list(set(glob.glob(p) for p in cleanup_patterns for p in glob.glob(p))) # glob them or let glob do it? Actually, glob.glob returns list, so we could just use cleanup_patterns in cleanup_glob
        # Wait, schedule_cleanup expects paths. But I can just pass paths_to_cleanup to upload_to_drive_with_retry.
        paths_to_cleanup = []
        for pattern in cleanup_patterns:
            paths_to_cleanup.extend(glob.glob(pattern))
        paths_to_cleanup = list(set(paths_to_cleanup))

        result = await upload_to_drive_with_retry(
            actual_path, drive_filename, state, updater_task, callback.message,
            reupload_metadata={
                "type": "yt",
                "actual_path": actual_path,
                "local_path": local_path,
                "drive_filename": drive_filename,
                "paths_to_cleanup": paths_to_cleanup
            },
            cleanup_paths=paths_to_cleanup,
        )
        if result is None:
            log.warning("Upload aborted or failed after retries")
            skip_cleanup = True
            return

        log.info("Upload complete: %s", result["view_link"])

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

        await safe_edit_caption_or_text(
            callback.message, final_text, parse_mode="HTML"
        )

    except Exception as exc:
        log.exception("YouTube processing failed")
        await safe_edit_caption_or_text(
            callback.message, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        updater_task.cancel()
        if not skip_cleanup:
            # Always clean up local files to prevent disk bloat
            # We need to clean up local_path, actual_path, and any other generated files like subtitles (.en.vtt, .srt)
            base_name = os.path.splitext(local_path)[0]
            cleanup_glob(
                local_path,
                actual_path if "actual_path" in locals() else local_path,
                f"{base_name}*.*",
                f"{local_path}*"
            )
