import asyncio
import logging
import secrets
import uuid

from aiogram.filters import Command
from aiogram.types import Message

from ..config import DOWNLOAD_DIR
from ..services.downloader import download_url
from ..services.encryptor import create_encrypted_zip
from ..services.progress import ProgressState, progress_updater
from ..services.semaphore import download_semaphore
from ..services.uploader import upload_to_drive_with_retry
from ..utils import (
    check_disk_space,
    cleanup_paths,
    escape_html,
    get_disk_free,
    is_allowed,
    safe_edit_caption_or_text,
)
from . import router

logger = logging.getLogger(__name__)

@router.message(Command("dl"))
async def cmd_dl(message: Message) -> None:
    """Download a URL, encrypt with AES-256 ZIP, and upload to Google Drive."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /dl [direct_link]\nExample: <code>/dl https://example.com/file.mp4</code>", parse_mode="HTML")
        return

    url = parts[1].strip()

    if not check_disk_space(DOWNLOAD_DIR):
        await message.answer(
            f"⚠️ Low disk space ({get_disk_free(DOWNLOAD_DIR)} free). "
            "Please try again later."
        )
        return

    request_id = uuid.uuid4().hex[:8]
    log = logger.getChild(f"dl.{request_id}")
    log.info("User %s started /dl: %s", message.from_user.id, url)

    status_msg = await message.answer("⏳ <b>Initializing download…</b>", parse_mode="HTML")

    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    local_path = None
    zip_path = None
    skip_cleanup = False

    if download_semaphore.locked():
        await safe_edit_caption_or_text(status_msg, "⏳ Server is busy. Your request is queued...", parse_mode="HTML")

    try:
        async with download_semaphore:
            # 1. Download
            local_path, original_filename = await download_url(url, state, prefix="dl")
            zip_path = f"{local_path}.zip"
            log.info("Download complete: %s", local_path)

        # 2. Encrypt — streaming AES-256 ZIP (64 KB chunks, constant RAM usage)
        state.action = "🔒 Encrypting..."
        state.percentage = 0.0
        state.speed = ""
        state.eta = ""

        password = secrets.token_urlsafe(12)

        await asyncio.to_thread(
            create_encrypted_zip,
            local_path,
            zip_path,
            password,
            original_filename,
            state
        )

        # 3. Upload to Google Drive
        drive_filename = f"{original_filename}.enc.zip"

        result = await upload_to_drive_with_retry(
            zip_path, drive_filename, state, updater_task, status_msg,
            reupload_metadata={
                "type": "dl",
                "zip_path": zip_path,
                "drive_filename": drive_filename,
                "password": password,
                "original_filename": original_filename,
                "local_path": local_path
            },
            cleanup_paths=[local_path, zip_path],
        )
        if result is None:
            log.warning("Upload aborted or failed after retries")
            skip_cleanup = True
            return

        log.info("Upload complete: %s", result["view_link"])

        state.done = True
        if not updater_task.done():
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
            f"<code>{escape_html(original_filename)}</code>\n\n"
            "<i>Open the .zip with 7-Zip or WinRAR, enter the password above,\n"
            "then rename the extracted file to the original filename.\n"
            "The filename inside the archive is intentionally generic to keep it private.</i>"
        )

        await safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        log.exception("DL command failed")
        await safe_edit_caption_or_text(
            status_msg, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()

        # Cleanup
        if not skip_cleanup:
            cleanup_paths(local_path, zip_path)

@router.message(Command("udl"))
async def cmd_udl(message: Message) -> None:
    """Download a URL without encryption and upload to Google Drive."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /udl [direct_link]\nExample: <code>/udl https://example.com/file.mp4</code>", parse_mode="HTML")
        return

    url = parts[1].strip()

    if not check_disk_space(DOWNLOAD_DIR):
        await message.answer(
            f"⚠️ Low disk space ({get_disk_free(DOWNLOAD_DIR)} free). "
            "Please try again later."
        )
        return

    request_id = uuid.uuid4().hex[:8]
    log = logger.getChild(f"udl.{request_id}")
    log.info("User %s started /udl: %s", message.from_user.id, url)

    status_msg = await message.answer("⏳ <b>Initializing download…</b>", parse_mode="HTML")

    state = ProgressState()
    updater_task = asyncio.create_task(progress_updater(status_msg, state))

    local_path = None
    skip_cleanup = False

    if download_semaphore.locked():
        await safe_edit_caption_or_text(status_msg, "⏳ Server is busy. Your request is queued...", parse_mode="HTML")

    try:
        async with download_semaphore:
            # 1. Download
            local_path, original_filename = await download_url(url, state, prefix="udl")
            log.info("Download complete: %s", local_path)

        # 2. Upload to Google Drive
        drive_filename = original_filename

        result = await upload_to_drive_with_retry(
            local_path, drive_filename, state, updater_task, status_msg,
            reupload_metadata={
                "type": "udl",
                "local_path": local_path,
                "drive_filename": drive_filename
            },
            cleanup_paths=[local_path],
        )
        if result is None:
            log.warning("Upload aborted or failed after retries")
            skip_cleanup = True
            return

        log.info("Upload complete: %s", result["view_link"])

        state.done = True
        if not updater_task.done():
            updater_task.cancel()

        # 3. Deliver
        view_link   = result["view_link"]
        direct_link = result["direct_link"]

        final_text = (
            "✅ <b>Upload complete!</b>\n\n"
            f"🔗 <a href='{view_link}'>Open in Google Drive</a>\n"
            f"⬇️ <a href='{direct_link}'>Direct Download Link</a>"
        )

        await safe_edit_caption_or_text(status_msg, final_text, parse_mode="HTML")

    except Exception as exc:
        log.exception("UDL command failed")
        await safe_edit_caption_or_text(
            status_msg, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML"
        )

    finally:
        state.done = True
        if not updater_task.done():
            updater_task.cancel()

        # Cleanup
        if not skip_cleanup:
            cleanup_paths(local_path)
