import asyncio

from aiogram.types import Message

from ..utils import safe_edit_caption_or_text


class ProgressState:
    def __init__(self):
        self.action = "Starting..."
        self.percentage = 0.0
        self.speed = ""
        self.eta = ""
        self.done = False

async def progress_updater(msg: Message, state: ProgressState):
    """Periodically edit message with current progress."""
    last_text = ""
    while not state.done:
        bar_len = 20
        filled_len = int(bar_len * state.percentage // 100)
        bar = "█" * filled_len + "░" * (bar_len - filled_len)

        text = f"<b>{state.action}</b>\n"
        text += f"<code>[{bar}] {state.percentage:.1f}%</code>\n"

        details = []
        if state.speed:
            details.append(f"Speed: {state.speed}")
        if state.eta:
            details.append(f"ETA: {state.eta}")

        if details:
            text += " · ".join(details)

        if text != last_text:
            await safe_edit_caption_or_text(msg, text, parse_mode="HTML")
            last_text = text

        await asyncio.sleep(2.5)  # Telegram limits message edits
