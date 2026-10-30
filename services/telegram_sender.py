import asyncio
from pathlib import Path
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from config.settings import settings
from services.content_detector import FileCategory
from services.temporary_link_server import TemporaryLinkServer
from utils.helpers import escape_html, format_seconds, format_size
from utils.logger import logger


class TelegramSender:
    """Delivers files to Telegram users using the appropriate Telegram API methods."""

    def __init__(self, bot: Bot):
        self.bot = bot

    def is_file_within_tg_limit(self, file_path: Path) -> bool:
        """Check if file size is within configured Telegram upload limits."""
        if not file_path.exists():
            return False
        max_bytes = settings.MAX_TG_UPLOAD_SIZE_MB * 1024 * 1024
        return file_path.stat().st_size <= max_bytes

    async def deliver_file(
        self,
        chat_id: int,
        file_path: Path,
        filename: str,
        category: FileCategory,
        caption: Optional[str] = None,
        job_id: Optional[str] = None,
        force_link: bool = False,
    ) -> bool:
        """
        Deliver a single file. If it exceeds Telegram limits or delivery fails,
        generate a private temporary download link.
        """
        file_size = file_path.stat().st_size if file_path.exists() else 0
        formatted_size = format_size(file_size)

        # Default caption
        clean_caption = caption or (
            f"✅ <b>{escape_html(filename)}</b>\n"
            f"📦 <b>Size:</b> <code>{formatted_size}</code>\n"
            f"📁 <b>Type:</b> {category.value.capitalize()}"
        )

        # Check size limit or user preference
        if force_link or not self.is_file_within_tg_limit(file_path):
            logger.info(
                f"File '{filename}' ({formatted_size}) exceeds Telegram limit ({settings.MAX_TG_UPLOAD_SIZE_MB}MB) "
                f"or link requested. Generating temporary download link..."
            )
            return await self.deliver_via_temp_link(
                chat_id=chat_id,
                file_path=file_path,
                filename=filename,
                job_id=job_id,
                reason="File size exceeds Telegram limit." if not force_link else "User preferred direct link.",
            )

        # Send directly to Telegram
        input_file = FSInputFile(path=str(file_path), filename=filename)

        for attempt in range(1, 4):
            try:
                if category == FileCategory.VIDEO:
                    await self.bot.send_video(
                        chat_id=chat_id,
                        video=input_file,
                        caption=clean_caption,
                        parse_mode="HTML",
                        supports_streaming=True,
                    )
                elif category == FileCategory.IMAGE:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=input_file,
                        caption=clean_caption,
                        parse_mode="HTML",
                    )
                elif category == FileCategory.AUDIO:
                    await self.bot.send_audio(
                        chat_id=chat_id,
                        audio=input_file,
                        caption=clean_caption,
                        title=filename,
                        parse_mode="HTML",
                    )
                else:
                    await self.bot.send_document(
                        chat_id=chat_id,
                        document=input_file,
                        caption=clean_caption,
                        parse_mode="HTML",
                    )
                return True

            except TelegramRetryAfter as e:
                logger.warning(f"Telegram flood wait: sleeping {e.retry_after} seconds...")
                await asyncio.sleep(e.retry_after)
            except TelegramAPIError as e:
                logger.warning(f"Telegram API delivery error (attempt {attempt}/3): {e}")
                if "file is too big" in str(e).lower() or attempt == 3:
                    # Fallback to temp link
                    return await self.deliver_via_temp_link(
                        chat_id=chat_id,
                        file_path=file_path,
                        filename=filename,
                        job_id=job_id,
                        reason="Telegram upload error or limit reached.",
                    )
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Unexpected error sending file to Telegram: {e}")
                return await self.deliver_via_temp_link(
                    chat_id=chat_id,
                    file_path=file_path,
                    filename=filename,
                    job_id=job_id,
                    reason=f"Delivery failure: {str(e)}",
                )

        return False

    async def deliver_via_temp_link(
        self,
        chat_id: int,
        file_path: Path,
        filename: str,
        job_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Create and send a temporary private download link."""
        try:
            download_url, expires_at = await TemporaryLinkServer.create_download_link(
                user_id=chat_id,
                file_path=file_path,
                filename=filename,
                expiry_hours=settings.TEMP_LINK_EXPIRY_HOURS,
                job_id=job_id,
            )

            file_size = format_size(file_path.stat().st_size if file_path.exists() else 0)
            expiry_str = expires_at.strftime("%Y-%m-%d %H:%M UTC")

            msg_text = (
                f"🔗 <b>Private Download Link Ready</b>\n\n"
                f"📄 <b>File:</b> <code>{escape_html(filename)}</code>\n"
                f"📦 <b>Size:</b> <code>{file_size}</code>\n"
                f"⏳ <b>Expires:</b> <code>{expiry_str}</code> (in {settings.TEMP_LINK_EXPIRY_HOURS} hours)\n"
            )
            if reason:
                msg_text += f"ℹ️ <i>Notice: {escape_html(reason)}</i>\n"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬇️ Download File", url=download_url)]
                ]
            )

            await self.bot.send_message(
                chat_id=chat_id,
                text=msg_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to generate and deliver temporary link: {e}")
            await self.bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Error:</b> Failed to deliver file {escape_html(filename)}. {escape_html(str(e))}",
                parse_mode="HTML",
            )
            return False

    async def deliver_media_group(
        self,
        chat_id: int,
        media_files: List[tuple[Path, FileCategory, str]],
    ) -> bool:
        """
        Send a batch of images and/or videos as a Telegram media group (max 10 items per group).
        """
        if not media_files:
            return True

        # Group in batches of 10
        chunk_size = 10
        for i in range(0, len(media_files), chunk_size):
            batch = media_files[i : i + chunk_size]
            media_list = []
            for path, cat, fname in batch:
                input_file = FSInputFile(path=str(path), filename=fname)
                if cat == FileCategory.VIDEO:
                    media_list.append(InputMediaVideo(media=input_file, caption=escape_html(fname)))
                else:
                    media_list.append(InputMediaPhoto(media=input_file, caption=escape_html(fname)))

            try:
                await self.bot.send_media_group(chat_id=chat_id, media=media_list)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error sending media group: {e}. Falling back to individual delivery.")
                for path, cat, fname in batch:
                    await self.deliver_file(chat_id, path, fname, cat)

        return True
