import asyncio
import logging
import re
import urllib.parse

import aiohttp
import feedparser
from aiogram.filters import Command
from aiogram.types import Message

from ..utils import escape_html, is_allowed, safe_edit_caption_or_text
from . import router

logger = logging.getLogger(__name__)

@router.message(Command("lookup_pod"))
async def cmd_lookup_pod(message: Message) -> None:
    """Search iTunes for podcasts matching a query."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /lookup_pod [query]\nExample: <code>/lookup_pod lex fridman</code>", parse_mode="HTML")
        return

    query = parts[1].strip()
    status_msg = await message.answer("🔍 <b>Searching for podcasts…</b>", parse_mode="HTML")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://itunes.apple.com/search?media=podcast&term={urllib.parse.quote(query)}&limit=5"
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)

        results = data.get("results", [])
        if not results:
            await safe_edit_caption_or_text(status_msg, "❌ No podcasts found.", parse_mode="HTML")
            return

        text = f"🎙 <b>Top 5 results for '{escape_html(query)}'</b>:\n\n"
        for i, res in enumerate(results, start=1):
            name = escape_html(res.get("collectionName", "Unknown Title"))
            author = escape_html(res.get("artistName", "Unknown Author"))
            feed = escape_html(res.get("feedUrl", ""))

            text += f"{i}. <b>{name}</b> by {author}\n"
            text += f"Feed: <code>{feed}</code>\n\n"

        await safe_edit_caption_or_text(status_msg, text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("lookup_pod failed")
        await safe_edit_caption_or_text(status_msg, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML")

@router.message(Command("pod"))
async def cmd_pod(message: Message) -> None:
    """Fetch the latest 5 episodes from a podcast RSS feed."""
    if not is_allowed(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ <b>Usage:</b> /pod [rss-link]\nExample: <code>/pod https://example.com/feed.xml</code>", parse_mode="HTML")
        return

    rss_url = parts[1].strip()
    status_msg = await message.answer("⏳ <b>Fetching podcast feed…</b>", parse_mode="HTML")

    try:
        def fetch_feed(url):
            return feedparser.parse(url)

        feed = await asyncio.to_thread(fetch_feed, rss_url)

        if getattr(feed, 'bozo', 0) == 1 and not feed.entries:
            # Maybe invalid feed
            await safe_edit_caption_or_text(status_msg, "❌ Could not parse the RSS feed.", parse_mode="HTML")
            return

        entries = feed.entries[:5]
        if not entries:
            await safe_edit_caption_or_text(status_msg, "❌ No episodes found in the feed.", parse_mode="HTML")
            return

        text = f"🎧 <b>Last 5 episodes of {escape_html(feed.feed.get('title', 'Unknown Podcast'))}</b>:\n\n"

        for i, entry in enumerate(entries, start=1):
            title = escape_html(entry.get("title", "Unknown Episode"))

            # Find the audio enclosure
            download_link = ""
            for link in entry.get("links", []):
                if link.get("rel") == "enclosure" and link.get("type", "").startswith("audio/"):
                    download_link = link.get("href")
                    break

            if not download_link and "link" in entry:
                download_link = entry.link

            # Truncate description and add collapsible block (blockquote)
            desc = entry.get("summary", "")
            # strip html tags
            desc = re.sub(r'<[^>]+>', '', desc)
            if len(desc) > 300:
                desc = desc[:300] + "..."
            desc = escape_html(desc)

            text += f"{i}. <b>{title}</b>\n"
            text += f"<blockquote expandable>{desc}</blockquote>\n"
            if download_link:
                text += f"⬇️ <a href='{download_link}'>Download / Listen</a>\n\n"
            else:
                text += "⬇️ No audio link found\n\n"

        await safe_edit_caption_or_text(status_msg, text, parse_mode="HTML")

    except Exception as exc:
        logger.exception("pod failed")
        await safe_edit_caption_or_text(status_msg, f"❌ Error: {escape_html(str(exc))}", parse_mode="HTML")
