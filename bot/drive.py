"""
drive.py — Google Drive upload and permission management via Service Account.
"""
import os
from typing import Optional

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import DRIVE_FOLDER_ID, SERVICE_ACCOUNT_PATH

TOKEN_PATH = "/config/token.json"


_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Module-level cached Drive service (one per container lifetime)
_drive_service = None


def _get_service():
    """Return (and cache) an authenticated Drive v3 service resource."""
    global _drive_service
    if _drive_service is None:
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
            
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _drive_service


def upload(filepath: str, filename: str, progress_hook=None) -> dict:
    """
    Upload *filepath* to Google Drive as *filename*.

    Steps:
    1. Upload with resumable=True (safe for large files).
    2. Set 'anyone can view' permission immediately.
    3. Return links dict.

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
    # 10MB chunk size for resumable uploads to allow progress reporting
    media = MediaFileUpload(filepath, mimetype=mime_type, resumable=True, chunksize=10 * 1024 * 1024)

    file_metadata: dict = {"name": filename}
    if DRIVE_FOLDER_ID:
        file_metadata["parents"] = [DRIVE_FOLDER_ID]

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
    while response is None:
        status, response = request.next_chunk()
        if status and progress_hook:
            progress_hook(status.progress())
            
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
