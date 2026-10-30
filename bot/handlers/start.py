from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from utils.helpers import escape_html

router = Router(name="start_router")


@router.message(Command("start"))
async def cmd_start(message: Message):
    user_name = message.from_user.first_name if message.from_user else "Friend"
    text = (
        f"👋 <b>Welcome, {escape_html(user_name)}!</b>\n\n"
        f"🚀 <b>Ultimate TeraBox Downloader Bot</b>\n"
        f"I can download and deliver files directly from TeraBox straight to Telegram.\n\n"
        f"✨ <b>Supported Content:</b>\n"
        f"• 🎬 Videos (MP4, MKV, AVI, etc.)\n"
        f"• 🖼 Images & Albums (JPG, PNG, WEBP, etc.)\n"
        f"• 🎵 Music & Audio (MP3, WAV, FLAC, etc.)\n"
        f"• 📄 Documents & Books (PDF, DOCX, TXT, etc.)\n"
        f"• 📁 Shared & Nested Folders (Packaged as ZIP)\n"
        f"• 🔗 Multiple Links in one request\n\n"
        f"💡 <b>How to Use:</b>\n"
        f"Simply send or forward any TeraBox share link (e.g. <code>https://terabox.com/s/XXXXXX</code>) "
        f"and I will analyze its contents for you!\n\n"
        f"🛠 <b>Available Commands:</b>\n"
        f"/help - Comprehensive user guide\n"
        f"/queue - View active & waiting download jobs\n"
        f"/history - View recent downloads\n"
        f"/settings - Delivery preferences\n"
        f"/status - System health & limits\n"
        f"/cancel - Cancel your ongoing downloads"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        f"📖 <b>TeraBox Downloader Guide</b>\n\n"
        f"<b>1. Sending Links:</b>\n"
        f"Send one or multiple TeraBox URLs in a single message. If your link is protected with a password, "
        f"add <code>?pwd=XXXX</code> or send <code>pwd: XXXX</code> in the message.\n\n"
        f"<b>2. Intelligent Detection:</b>\n"
        f"• <b>Single Video:</b> Downloads and delivers directly as a streaming Telegram video.\n"
        f"• <b>Multiple Files / Folders:</b> Opens an interactive file browser where you can navigate subfolders, "
        f"select specific files, or download all at once.\n\n"
        f"<b>3. Large Files (>50 MB):</b>\n"
        f"Files exceeding Telegram's file size limit are automatically provided with a private, high-speed, "
        f"resumable temporary download link (valid for 6 hours).\n\n"
        f"<b>4. Folder Downloads:</b>\n"
        f"When downloading a folder, the bot preserves the exact folder structure and packages it cleanly into a ZIP archive.\n\n"
        f"<b>5. Managing Downloads:</b>\n"
        f"Use /queue to track progress, /cancel to stop active downloads, or /history to view past files."
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("about"))
async def cmd_about(message: Message):
    text = (
        f"ℹ️ <b>About TeraBox Downloader</b>\n\n"
        f"• <b>Engine:</b> Asynchronous Python (aiogram 3.x, FastAPI, aiohttp)\n"
        f"• <b>Streaming:</b> Low-memory chunked streaming with HTTP Range resume\n"
        f"• <b>Security:</b> Full SSRF mitigation & anti-path-traversal protection\n"
        f"• <b>Archive:</b> In-memory streaming ZIP generation with UTF-8 & deduplication\n"
        f"• <b>Speed:</b> Multi-connection concurrent worker pool"
    )
    await message.answer(text, parse_mode="HTML")
