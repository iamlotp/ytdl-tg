"""
drive.py — Google Drive upload and permission management via Service Account.
"""
import os
import threading
import time
from collections.abc import Callable

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .config import DRIVE_FOLDER_ID, SERVICE_ACCOUNT_PATH, TOKEN_PATH

_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Thread-local storage for Drive service (google-api-python-client is not thread-safe)
_thread_local = threading.local()

# Module-level cached subfolder ID for Telegram uploads
_telegram_subfolder_id: str | None = None


def _get_service():
    """Return (and cache) an authenticated Drive v3 service resource per thread."""
    if not hasattr(_thread_local, "drive_service"):
        if os.path.isfile(TOKEN_PATH):
            # User OAuth authentication (recommended for standard Google accounts)
            creds = OAuthCredentials.from_authorized_user_file(TOKEN_PATH, _SCOPES)
        elif os.path.isfile(SERVICE_ACCOUNT_PATH):
            # Service Account authentication
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_PATH, scopes=_SCOPES
            )
        else:
            raise RuntimeError(
                f"No credentials found. Mount your OAuth token.json to {TOKEN_PATH} "
                f"or your service account JSON to {SERVICE_ACCOUNT_PATH}."
            )

        _thread_local.drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _thread_local.drive_service


def get_or_create_subfolder(folder_name: str, parent_id: str | None = None) -> str:
    """
    Return the ID of a subfolder named *folder_name* inside the parent.
    Creates the subfolder if it does not already exist.  The result is
    cached for the lifetime of the process.
    """
    global _telegram_subfolder_id
    if _telegram_subfolder_id is not None:
        return _telegram_subfolder_id

    service = _get_service()
    parent = parent_id or DRIVE_FOLDER_ID

    # Search for an existing subfolder
    query = (
        f"name = '{folder_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    if parent:
        query += f" and '{parent}' in parents"

    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )

    files = results.get("files", [])
    if files:
        _telegram_subfolder_id = files[0]["id"]
        return _telegram_subfolder_id

    # Create the subfolder
    metadata: dict = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent:
        metadata["parents"] = [parent]

    folder = (
        service.files()
        .create(body=metadata, fields="id", supportsAllDrives=True)
        .execute()
    )

    _telegram_subfolder_id = folder["id"]
    return _telegram_subfolder_id


def upload(
    filepath: str,
    filename: str,
    progress_hook: Callable[[float], None] | None = None,
    folder_id: str | None = None,
) -> dict[str, str]:
    """
    Upload *filepath* to Google Drive as *filename*.

    Steps:
    1. Upload with resumable=True (safe for large files).
    2. Set 'anyone can view' permission immediately.
    3. Return links dict.

    Args:
        folder_id: Override the target Drive folder.  If *None*, falls back
                   to the global DRIVE_FOLDER_ID.

    Returns:
        {
          "file_id":     str,
          "view_link":   str,   # webViewLink — opens in Drive viewer
          "direct_link": str,   # /uc?export=download — triggers download
                                # NOTE: >100 MB files will hit a Google
                                # virus-scan interstitial; this is a hard
                                # server-side constraint and cannot be bypassed.
        }

    This is a synchronous blocking call — run it in asyncio.to_thread().
    """
    service = _get_service()

    mime_type = _guess_mime(filename)

    with open(filepath, 'rb') as fd:
        # 10MB chunk size for resumable uploads to allow progress reporting
        media = MediaIoBaseUpload(fd, mimetype=mime_type, resumable=True, chunksize=10 * 1024 * 1024)

        file_metadata: dict = {"name": filename}
        target_folder = folder_id or DRIVE_FOLDER_ID
        if target_folder:
            file_metadata["parents"] = [target_folder]

        # Upload
        request = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
        )

        response = None
        retries = 0
        max_retries = 5

        while response is None:
            try:
                # num_retries=3 handles some HTTP-level errors automatically
                status, response = request.next_chunk(num_retries=3)
                if status and progress_hook:
                    progress_hook(status.progress())
                retries = 0  # Reset retries after a successful chunk
            except (OSError, BrokenPipeError, ConnectionResetError, ConnectionError, TimeoutError):
                retries += 1
                if retries > max_retries:
                    raise
                time.sleep(2 ** retries)
            except HttpError as e:
                if e.resp.status in [403, 500, 502, 503, 504]:
                    retries += 1
                    if retries > max_retries:
                        raise
                    time.sleep(2 ** retries)
                else:
                    raise

        uploaded = response

    file_id: str = uploaded["id"]
    view_link: str = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")

    # Make the file publicly readable
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True,
    ).execute()

    direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"

    return {
        "file_id":     file_id,
        "view_link":   view_link,
        "direct_link": direct_link,
    }


def _guess_mime(filename: str) -> str:
    """Return an appropriate MIME type based on the file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }.get(ext, "application/octet-stream")
