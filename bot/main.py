"""
main.py — Bot entry point.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import BOT_TOKEN, DOWNLOAD_DIR, TELEGRAM_API_ID, TELEGRAM_API_HASH
from .handlers import router
from . import telegram_client


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Ensure the local staging directory exists
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    logger.info("Download staging directory: %s", DOWNLOAD_DIR)

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

