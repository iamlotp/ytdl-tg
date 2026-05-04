"""
uploader.py — Shared Google Drive upload logic with retry handling.
"""
import asyncio
import logging
import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..drive import drive
from ..handlers.reupload import REUPLOAD_JOBS, schedule_cleanup
from ..utils import escape_html, safe_edit_caption_or_text

logger = logging.getLogger(__name__)


async def upload_to_drive_with_retry(
    file_path: str,
    drive_filename: str,
    state,
    updater_task: asyncio.Task,
    status_msg: Message,
    reupload_metadata: dict,
    folder_id: str | None = None,
    cleanup_paths: list[str] | None = None,
) -> dict | None:
    """Upload to Drive. On failure, create a reupload job and return None.

    On success, returns the Drive result dict with view_link / direct_link.
    """
    def drive_progress_hook(progress: float):
        state.action = "☁️ Uploading to Google Drive..."
        state.percentage = progress * 100
        state.speed = ""
        state.eta = ""

    try:
        result = await asyncio.to_thread(
            drive.upload, file_path, drive_filename, drive_progress_hook, folder_id
        )
        return result
    except Exception as upload_exc:
        logger.error("Upload failed: %s", upload_exc, exc_info=True)
        state.done = True
        if updater_task and not updater_task.done():
            updater_task.cancel()

        job_id = uuid.uuid4().hex[:8]
        reupload_metadata["drive_filename"] = drive_filename
        REUPLOAD_JOBS[job_id] = reupload_metadata

        asyncio.create_task(schedule_cleanup(job_id, cleanup_paths or [file_path]))

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Reupload", callback_data=f"reup|{job_id}")
        ]])

        await safe_edit_caption_or_text(
            status_msg,
            f"❌ Upload failed: {escape_html(str(upload_exc))}\n\n"
            "<i>File kept on server for 60m. Click Reupload to try again.</i>",
            parse_mode="HTML", reply_markup=keyboard
        )
        return None
