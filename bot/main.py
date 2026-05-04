"""
main.py — Bot entry point.
"""
import asyncio
import glob
import logging
import os
import signal
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from . import telegram_client
from .config import BOT_TOKEN, DOWNLOAD_DIR, TELEGRAM_API_HASH, TELEGRAM_API_ID
from .handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

active_tasks: set[asyncio.Task] = set()

async def periodic_cleanup(directory: str, max_age_hours: int = 6):
    """Delete files in `directory` older than `max_age_hours`."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        for filepath in glob.glob(os.path.join(directory, "*")):
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                try:
                    os.remove(filepath)
                    logger.info("Auto-cleanup: removed stale file %s", filepath)
                except OSError:
                    pass


async def main() -> None:
    # Ensure the local staging directory exists
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    logger.info("Download staging directory: %s", DOWNLOAD_DIR)

    asyncio.create_task(periodic_cleanup(DOWNLOAD_DIR))

    # Register shutdown handler
    loop = asyncio.get_running_loop()

    async def shutdown():
        logger.info("Shutting down: cancelling %d active tasks", len(active_tasks))
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*active_tasks, return_exceptions=True)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    # Initialize Telethon MTProto client for large file downloads
    if TELEGRAM_API_ID and TELEGRAM_API_HASH:
        await telegram_client.init_client(TELEGRAM_API_ID, TELEGRAM_API_HASH, BOT_TOKEN)
    else:
        logger.warning(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH not set — "
            "file-to-Drive feature will be disabled"
        )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting bot polling…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await telegram_client.stop_client()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())

