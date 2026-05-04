import os

import pyzipper

from .progress import ProgressState


def create_encrypted_zip(source_path: str, zip_path: str, password: str, original_filename: str, progress_state: ProgressState = None):
    """Write an AES-256 encrypted ZIP in 64 KB chunks via zf.open().

    - Uses pyzipper's streaming write API (never loads full file into RAM)
    - Manual chunk loop so we can report progress to state.percentage
    - Manual try/finally close to absorb pyzipper's spurious
      'open writing handle' ValueError on close
    """
    file_size = os.path.getsize(source_path)
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
            with open(source_path, 'rb') as src:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dest.write(chunk)
                    encrypted += len(chunk)
                    if file_size > 0 and progress_state:
                        progress_state.percentage = (encrypted / file_size) * 100
    finally:
        try:
            zf.close()
        except ValueError as ve:
            if "open writing handle" not in str(ve):
                raise
