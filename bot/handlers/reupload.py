import asyncio
import logging

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from .. import drive
from ..services.progress import ProgressState, progress_updater
from ..utils import cleanup_paths, escape_html, format_size, is_allowed, safe_edit_caption_or_text
from . import router

logger = logging.getLogger(__name__)

REUPLOAD_JOBS = {}

async def schedule_cleanup(job_id: str, paths_to_cleanup: list, delay: int = 3600):
    await asyncio.sleep(delay)
    if job_id in REUPLOAD_JOBS:
        cleanup_paths(*paths_to_cleanup)
        del REUPLOAD_JOBS[job_id]

@router.callback_query(F.data.startswith("reup|"))
async def handle_reupload(callback: CallbackQuery) -> None:
    """Handle callback to retry an upload that previously failed."""
    await callback.answer()
    if not is_allowed(callback.from_user.id):
        return

    parts = callback.data.split("|")
    if len(parts) != 2:
        await callback.answer("⚠️ Invalid job.", show_alert=True)
        return

    job_id = parts[1]
    job = REUPLOAD_JOBS.get(job_id)

    if not job:
        await callback.answer("⚠️ This reupload job has expired or does not exist.", show_alert=True)
        return

    status_msg = callback.message
    await safe_edit_caption_or_text(status_msg, "⏳ <b>Retrying upload to Google Drive…</b>", parse_mode="HTML")

    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    def drive_progress_hook(progress: float):
        state.action = "☁️ Uploading to Google Drive..."
        state.percentage = progress * 100
        state.speed = ""
        state.eta = ""

    try:
        # Determine paths based on job type
        if job["type"] == "dl":
            result = await asyncio.to_thread(
                drive.upload, job["zip_path"], job["drive_filename"], drive_progress_hook
            )
            view_link = result["view_link"]
            direct_link = result["direct_link"]
            final_text = (
                "✅ <b>Upload complete!</b>\n\n"
                f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
                f"⬇️ <a href='{direct_link}'>Direct Download Link</a>\n\n"
                "🔑 <b>Password to extract:</b>\n"
                f"<code>{job['password']}</code>\n\n"
                "📄 <b>Original filename:</b>\n"
                f"<code>{escape_html(job['original_filename'])}</code>"
            )

        elif job["type"] == "udl":
            result = await asyncio.to_thread(
                drive.upload, job["local_path"], job["drive_filename"], drive_progress_hook
            )
            view_link = result["view_link"]
            direct_link = result["direct_link"]
            final_text = (
                "✅ <b>Upload complete!</b>\n\n"
                f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
                f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
            )

        elif job["type"] == "yt":
            result = await asyncio.to_thread(
                drive.upload, job["actual_path"], job["drive_filename"], drive_progress_hook
            )
            view_link = result["view_link"]
            direct_link = result["direct_link"]
            final_text = (
                "✅ <b>Upload complete!</b>\n\n"
                f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
                f"⬇️ <a href='{direct_link}'>Direct Download Link</a>\n\n"
                "<i>Note: Files larger than 100 MB may show a Google virus-scan "
                "warning page before downloading — this is a Google limitation.</i>"
            )

        elif job["type"] == "tg":
            result = await asyncio.to_thread(
                drive.upload, job["actual_path"], job["drive_filename"], drive_progress_hook, job["subfolder_id"]
            )
            view_link = result["view_link"]
            direct_link = result["direct_link"]
            final_text = (
                "✅ <b>Upload complete!</b>\n\n"
                f"📄 <b>File:</b> <code>{escape_html(job['file_name'])}</code>\n"
                f"📦 <b>Size:</b> {format_size(job['file_size'])}\n\n"
                f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
                f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
            )
        else:
            raise ValueError("Unknown job type")

        state.done = True
        updater_task.cancel()
        await safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

        # Immediate cleanup on success
        if "paths_to_cleanup" in job:
            cleanup_paths(*job["paths_to_cleanup"])
        else:
            cleanup_paths(job.get("local_path"), job.get("zip_path"), job.get("actual_path"))
        del REUPLOAD_JOBS[job_id]

    except Exception as exc:
        state.done = True
        updater_task.cancel()
        logger.exception("Reupload failed")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Reupload", callback_data=f"reup|{job_id}")
        ]])
        await safe_edit_caption_or_text(
            status_msg, f"❌ Reupload failed: {escape_html(str(exc))}\n\n<i>File is still on server. Click Reupload to try again.</i>", parse_mode="HTML", reply_markup=keyboard
        )
