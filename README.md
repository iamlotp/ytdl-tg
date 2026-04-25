# YouTube → Google Drive Telegram Bot

A Dockerized Telegram bot that downloads YouTube videos (via **yt-dlp** + **ffmpeg**) and uploads them to **Google Drive** via a Service Account, then sends back shareable and direct-download links.

---

## Features

| Feature | Detail |
|---|---|
| Quality selection | 1440p (2K) · 1080p · 720p · 480p · MP3 Audio |
| Live Progress Bar | Real-time downloading & uploading progress, speed, and ETA |
| Dynamic Naming | Files are natively named `[Title] - [Quality].[ext]` on Drive |
| Live stream guard | Live streams are detected and rejected before download |
| Whitelist | Only pre-approved Telegram user IDs can use the bot |
| Disk safety | `try/finally` deletes local files after upload; tmpfs staging directory |
| Age-restricted | Optional `cookies.txt` mount for bypassing soft age gates |

---

## Available Commands

- `/start` - Displays welcome message and supported formats
- `/help` - Displays the list of available commands
- `/dl [link]` - Downloads a file from a direct link, encrypts it with AES-256 password-protected ZIP, and uploads it to Google Drive
- `/udl [link]` - Downloads a file from a direct link *without* encryption and uploads it directly to Google Drive
- `/lookup_pod [query]` - Searches the iTunes directory for podcasts matching the query and returns the top 5 results with their RSS feeds
- `/pod [rss-link]` - Fetches the 5 most recent episodes from a podcast RSS feed with audio download links
- **Send a YouTube link** - Interactive menu to select video quality or extract audio, then directly uploads the result to Google Drive

---

## Prerequisites

- Docker & Docker Compose v2
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- A Google Cloud **Service Account** with Google Drive API enabled

---

## Setup

### 1. Google Drive Authentication

Google recently changed policies: **Service Accounts now have a 0-byte storage limit** and cannot upload files to a standard "My Drive" folder. 
Depending on your Google account type, you must pick one of two methods:

#### Option A: OAuth User Token (Required for free @gmail.com accounts)
Use this if you are using a standard free Google account and want to upload to your personal 15GB Drive.
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **OAuth consent screen**. Set up an External app and add your email to **Test users**.
2. Go to **Credentials** → **Create Credentials** → **OAuth client ID** (Application type: **Desktop app**).
3. Download the JSON, rename it to `credentials.json`, and place it in the project root.
4. Run the helper script on your server: `python3 generate_token.py`
5. Follow the prompt to log in with your Google account. It will output `token.json`.
6. Move `token.json` into the `./config` folder.

#### Option B: Service Account (Requires a Google Workspace Shared Drive)
Use this only if you have a Google Workspace account and are uploading into a **Shared Drive** (Service Accounts have unlimited upload rights inside Shared Drives).
1. Go to Google Cloud Console → **Service Accounts**, create one, and download the JSON key.
2. Rename it to `service_account.json` and place it in the `./config` folder.
3. Share your target Shared Drive folder with the Service Account email.

### 2. Configure the bot

```bash
# Clone / enter the project directory
cd ytdl-tg
mkdir -p config

# Copy and fill in the environment file
cp .env.example .env
nano .env
```

Fill in `.env`:

```env
BOT_TOKEN=123456:ABCdef...           # from @BotFather
WHITELIST_IDS=123456789              # your Telegram user ID (get it from @userinfobot)
DRIVE_FOLDER_ID=1aBcDeFgHiJk...      # Folder ID inside your Drive / Shared Drive
```

### 3. (Optional) Cookies for age-restricted content

Export your browser cookies for youtube.com in **Netscape format** (e.g. using the "Get cookies.txt LOCALLY" Chrome extension):

```bash
mkdir -p cookies
cp ~/Downloads/cookies.txt cookies/cookies.txt
```

### 4. Launch

```bash
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f
```

---

## Google Drive — 100 MB Direct Link Limit

> **Important:** Google Drive enforces a virus-scan interstitial page for files **larger than 100 MB** when using the `/uc?export=download` direct link. The bot always generates this URL correctly, but large files will route through a "Can't scan for viruses" warning page where the user must click a confirmation button to proceed. This is a hard server-side constraint enforced by Google and **cannot be bypassed**.

For large files, use the **"Open in Google Drive"** viewer link instead and download from there.

---

## Updating yt-dlp

YouTube frequently changes its internal API. If downloads stop working, update yt-dlp inside the running container:

```bash
docker compose exec ytdl-bot pip install -U yt-dlp
```

Or rebuild the image to get the latest version:

```bash
docker compose up -d --build
```

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `WHITELIST_IDS` | ✅ | Comma-separated allowed Telegram user IDs |
| `DRIVE_FOLDER_ID` | ✅ | Target Google Drive folder ID |
| `SERVICE_ACCOUNT_PATH` | ❌ | Path to service account JSON (default: `/config/service_account.json`) |
| `TOKEN_PATH` | ❌ | Path to OAuth token.json (default: `/config/token.json`) |
| `COOKIES_PATH` | ❌ | Path to cookies.txt (default: `/cookies/cookies.txt`) |
| `DOWNLOAD_DIR` | ❌ | Temp download dir inside container (default: `/tmp/ytdl`) |
