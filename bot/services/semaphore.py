import asyncio

from ..config import MAX_CONCURRENT_DOWNLOADS

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
