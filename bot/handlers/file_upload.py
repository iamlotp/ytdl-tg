import asyncio
import logging
import os
import re
import uuid

from aiogram import F
from aiogram.types import Message

from .. import drive
from ..config import DOWNLOAD_DIR
from ..services.progress import ProgressState, progress_updater
from ..services.semaphore import download_semaphore
from ..services.uploader import upload_to_drive_with_retry
from ..utils import (
    check_disk_space,
    cleanup_paths,
    escape_html,
    format_size,
    get_disk_free,
    is_allowed,
    safe_edit_caption_or_text,
)
from . import router

logger = logging.getLogger(__name__)

@router.message(F.document | F.photo | F.video | F.audio | F.voice | F.video_note)
async def handle_file_to_drive(message: Message) -> None:
    """Receive any file sent to the bot, download via MTProto, upload to Drive."""
    if not is_allowed(message.from_user.id):
        return

    # Ensure the Telethon client is available
    from .. import telegram_client
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

    if not check_disk_space(DOWNLOAD_DIR, required_bytes=file_size + (50 * 1024 * 1024)): # file size + 50MB buffer
        await message.answer(
            f"⚠️ Low disk space ({get_disk_free(DOWNLOAD_DIR)} free). "
            "Please try again later."
        )
        return

    status_msg = await message.answer(
        f"📥 <b>Receiving file:</b> <code>{escape_html(file_name)}</code>\n"
        f"📦 <b>Size:</b> {format_size(file_size)}",
        parse_mode="HTML",
    )

    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    unique_id = uuid.uuid4().hex[:8]
    log = logger.getChild(f"tg.{unique_id}")
    log.info("User %s uploading file: %s (size: %s)", message.from_user.id, file_name, file_size)

    local_path = os.path.join(DOWNLOAD_DIR, f"tg_{unique_id}_{file_name}")
    actual_path = local_path  # Telethon may return a slightly different path
    skip_cleanup = False

    if download_semaphore.locked():
        await safe_edit_caption_or_text(status_msg, "⏳ Server is busy. Your request is queued...", parse_mode="HTML")

    try:
        async with download_semaphore:
            # 1. Download from Telegram via MTProto
            state.action = "⬇️ Downloading from Telegram..."
            state.percentage = 0.0

        def tg_progress(current, total):
            if total > 0:
                state.percentage = (current / total) * 100
                state.speed = f"{format_size(current)} / {format_size(total)}"

        actual_path = await telegram_client.download_file(
            chat_id=message.chat.id,
            message_id=message.message_id,
            destination=local_path,
            progress_callback=tg_progress,
        )
        log.info("Download complete: %s", actual_path)

        # 2. Upload to Google Drive
        subfolder_id = await asyncio.to_thread(
            drive.get_or_create_subfolder, "Telegram Uploads"
        )

        result = await upload_to_drive_with_retry(
            actual_path, file_name, state, updater_task, status_msg,
            reupload_metadata={
                "type": "tg",
                "actual_path": actual_path,
                "local_path": local_path,
                "drive_filename": file_name,
                "file_name": file_name,
                "file_size": file_size,
                "subfolder_id": subfolder_id
            },
            folder_id=subfolder_id,
            cleanup_paths=[local_path, actual_path],
        )
        if result is None:
            log.warning("Upload aborted or failed after retries")
            skip_cleanup = True
            return

        log.info("Upload complete: %s", result["view_link"])

        state.done = True
        if not updater_task.done():
            updater_task.cancel()

        # 3. Reply with links
        view_link = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"📄 <b>File:</b> <code>{escape_html(file_name)}</code>\n"
            f"📦 <b>Size:</b> {format_size(file_size)}\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
        )

        await safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        log.exception("File-to-Drive upload failed")
        state.done = True
        updater_task.cancel()
        await safe_edit_caption_or_text(
            status_msg, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()
        # Cleanup — delete both the expected and actual paths (may differ)
        if not skip_cleanup:
            cleanup_paths(local_path, actual_path)
