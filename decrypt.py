#!/usr/bin/env python3
"""
decrypt.py — Decrypt files produced by the ytdl-tg /dl command.

Usage:
    python3 decrypt.py <input.enc> <output_file> <password>

Example:
    python3 decrypt.py video.mp4.enc video.mp4 mySecretPassword
"""
import sys
import os

def decrypt_file(enc_path: str, out_path: str, password: str) -> None:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        print("Missing dependency. Install it with:\n  pip install cryptography")
        sys.exit(1)

    CHUNK = 65536  # 64 KB — same as encryption

    with open(enc_path, "rb") as src:
        salt = src.read(16)
        nonce = src.read(16)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=200_000,
            backend=default_backend(),
        )
        key = kdf.derive(password.encode("utf-8"))

        decryptor = Cipher(
            algorithms.AES(key), modes.CTR(nonce), backend=default_backend()
        ).decryptor()

        with open(out_path, "wb") as dst:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(decryptor.update(chunk))
            dst.write(decryptor.finalize())

    print(f"✅ Decrypted → {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    enc_path, out_path, password = sys.argv[1], sys.argv[2], sys.argv[3]

    if not os.path.isfile(enc_path):
        print(f"❌ File not found: {enc_path}")
        sys.exit(1)

    decrypt_file(enc_path, out_path, password)
