import os

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# Comma-separated list of allowed Telegram user IDs
_raw_whitelist = os.environ.get("WHITELIST_IDS", "")
WHITELIST_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_whitelist.split(",") if uid.strip()
}

# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------
DRIVE_FOLDER_ID: str | None = os.environ.get("DRIVE_FOLDER_ID") or None

# Path inside the container (mounted via Docker volume)
SERVICE_ACCOUNT_PATH: str = os.environ.get(
    "SERVICE_ACCOUNT_PATH", "/config/service_account.json"
)
TOKEN_PATH: str = os.environ.get("TOKEN_PATH", "/config/token.json")

# ---------------------------------------------------------------------------
# Optional cookies for age-restricted content
# ---------------------------------------------------------------------------
COOKIES_PATH: str = os.environ.get("COOKIES_PATH", "/cookies/cookies.txt")

# ---------------------------------------------------------------------------
# Local download staging directory
# ---------------------------------------------------------------------------
DOWNLOAD_DIR: str = os.environ.get("DOWNLOAD_DIR", "/tmp/ytdl")

# Maximum concurrent download+upload operations
MAX_CONCURRENT_DOWNLOADS: int = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3"))

# ---------------------------------------------------------------------------
# Telegram MTProto credentials (for large file downloads via Telethon)
# ---------------------------------------------------------------------------
TELEGRAM_API_ID: int = int(os.environ.get("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH: str = os.environ.get("TELEGRAM_API_HASH", "")
