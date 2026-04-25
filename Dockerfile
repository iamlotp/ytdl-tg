# ── Stage: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install ffmpeg (stream merging) and nodejs (yt-dlp needs a JS runtime
# to solve YouTube's "n parameter" challenge — without it no video/audio
# formats are returned).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source
COPY bot/ ./bot/

# Create directories for mounts (avoids permission errors if volumes are omitted)
RUN mkdir -p /config /cookies /tmp/ytdl

CMD ["python", "-m", "bot.main"]
