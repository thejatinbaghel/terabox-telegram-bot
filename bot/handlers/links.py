import uuid
from typing import Dict, List
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.database import get_session
from database.repositories import UserRepository
from services.content_detector import ContentDetector, ContentScenario, FileCategory, TeraBoxItem
from services.file_browser import FileBrowserUI, browser_sessions
from services.link_parser import LinkParser, ParsedTeraBoxLink
from services.queue_manager import QueueManager
from services.terabox_client import TeraBoxClient
from utils.helpers import escape_html, format_size
from utils.logger import logger

router = Router(name="links_router")

# Cache for multi-link batches
multi_link_batches: Dict[str, List[ParsedTeraBoxLink]] = {}


@router.message(F.text)
async def handle_incoming_text_with_links(message: Message, queue_mgr: QueueManager):
    """Detects and processes single or multiple TeraBox sharing links."""
    text = message.text or ""
    parsed_links = LinkParser.parse_all(text)

    if not parsed_links:
        return  # Ignore messages without valid TeraBox links

    user_id = message.from_user.id if message.from_user else 0

    # Fetch user preferred delivery method
    delivery_pref = "telegram"
    async with get_session() as session:
        user = await UserRepository.get_user(session, user_id)
        if user:
            delivery_pref = user.preferred_delivery

    # Case 1: Multiple TeraBox links in one message
    if len(parsed_links) > 1:
        await handle_multiple_links(message, parsed_links, queue_mgr)
        return

    # Case 2: Single TeraBox link
    single_link = parsed_links[0]
    await handle_single_link(message, single_link, queue_mgr, delivery_pref)


async def handle_single_link(
    message: Message,
    link: ParsedTeraBoxLink,
    queue_mgr: QueueManager,
    delivery_pref: str,
):
    status_msg = await message.answer("🔍 <b>Analyzing TeraBox link...</b>", parse_mode="HTML")

    client = TeraBoxClient()
    result = await client.get_content_unwrapped(link.clean_url, password=link.password)

    if not result.success:
        await status_msg.edit_text(
            f"❌ <b>Failed to access link:</b>\n{escape_html(result.errmsg)}\n\n"
            f"<i>If this link is password-protected, send it with <code>?pwd=XXXX</code></i>",
            parse_mode="HTML",
        )
        return

    items = result.items
    if not items:
        await status_msg.edit_text("⚠️ <b>No accessible files or folders found in this link.</b>", parse_mode="HTML")
        return

    analysis = ContentDetector.analyze(items)
    user_id = message.from_user.id if message.from_user else 0

    files = [it for it in items if not it.is_dir]
    folders = [it for it in items if it.is_dir]

    # Content-first: If all items are files (or unwrapped folder contents), queue them instantly without showing folder/browser
    if len(folders) == 0 and len(files) > 0:
        if len(files) == 1:
            single_item = files[0]
            size_str = format_size(single_item.size)
            cat_icon = single_item.icon
            cat_name = single_item.category.value.capitalize()
            await status_msg.edit_text(
                f"{cat_icon} <b>Single {cat_name} Detected!</b>\n\n"
                f"📄 <b>File:</b> <code>{escape_html(single_item.filename)}</code>\n"
                f"📦 <b>Size:</b> <code>{size_str}</code>\n\n"
                f"🚀 <i>Adding {single_item.category.value} to download queue...</i>",
                parse_mode="HTML",
            )
            try:
                job_id = await queue_mgr.add_job(
                    user_id=user_id,
                    source_url=link.clean_url,
                    filename=single_item.filename,
                    file_size=single_item.size,
                    fs_id=single_item.fs_id,
                    category=single_item.category.value,
                    delivery_method=delivery_pref,
                    dir_path=result.current_dir,
                )
                pos = await queue_mgr.get_queue_position(job_id)
                await status_msg.edit_text(
                    f"✅ <b>Job Enqueued!</b>\n\n"
                    f"{cat_icon} <b>File:</b> <code>{escape_html(single_item.filename)}</code>\n"
                    f"📦 <b>Size:</b> <code>{size_str}</code>\n"
                    f"🔢 <b>Queue Position:</b> #{pos if pos > 0 else 'Starting now'}\n\n"
                    f"<i>You will receive the file once download completes.</i>",
                    parse_mode="HTML",
                )
            except Exception as e:
                await status_msg.edit_text(f"❌ <b>Error:</b> {escape_html(str(e))}", parse_mode="HTML")
            return
        else:
            total_size = sum(f.size for f in files)
            await status_msg.edit_text(
                f"🚀 <b>Found {len(files)} files ({format_size(total_size)})!</b>\n\n"
                f"<i>Queueing all content for download instantly...</i>",
                parse_mode="HTML",
            )
            enqueued = 0
            errors = []
            for it in files:
                try:
                    await queue_mgr.add_job(
                        user_id=user_id,
                        source_url=link.clean_url,
                        filename=it.filename,
                        file_size=it.size,
                        fs_id=it.fs_id,
                        category=it.category.value,
                        delivery_method=delivery_pref,
                        dir_path=result.current_dir,
                    )
                    enqueued += 1
                except Exception as e:
                    errors.append(str(e))
                    break

            summary = (
                f"✅ <b>{enqueued}/{len(files)} Files Enqueued!</b>\n\n"
                f"📦 <b>Total Size:</b> {format_size(total_size)}\n"
                f"📂 <b>Path:</b> <code>{escape_html(result.current_dir if result.current_dir != '/' else 'Root')}</code>\n\n"
                f"<i>Files will be delivered automatically as downloads finish.</i>"
            )
            if errors:
                summary += f"\n\n⚠️ <i>Queue note: {escape_html(errors[0])}</i>"
            await status_msg.edit_text(summary, parse_mode="HTML")
            return

    # Scenario: Multiple folders exist (user needs to choose folder)
    session = browser_sessions.create_session(
        user_id=user_id,
        source_url=link.clean_url,
        surl=link.surl,
        title=result.title or items[0].filename,
        items=items,
        current_dir=result.current_dir,
    )

    text = FileBrowserUI.render_text(session)
    keyboard = FileBrowserUI.build_keyboard(session)

    await status_msg.edit_text(text=text, parse_mode="HTML", reply_markup=keyboard)


async def handle_multiple_links(
    message: Message,
    links: List[ParsedTeraBoxLink],
    queue_mgr: QueueManager,
):
    """Scenario: Multiple links received at once."""
    batch_id = uuid.uuid4().hex[:8]
    multi_link_batches[batch_id] = links

    text = (
        f"🔗 <b>Multiple TeraBox Links Detected!</b>\n\n"
        f"<b>Links Received:</b> <code>{len(links)}</code>\n\n"
    )

    for i, lnk in enumerate(links[:8], 1):
        text += f"• [Link {i}] <code>{lnk.domain}/s/{lnk.raw_surl[:10]}...</code> - <b>Ready</b>\n"

    if len(links) > 8:
        text += f"<i>...and {len(links) - 8} more</i>\n"

    text += "\nWould you like to enqueue all links for downloading?"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⬇️ Download All ({len(links)} Links)",
                    callback_data=f"batch_dl:{batch_id}",
                )
            ],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"batch_cancel:{batch_id}")],
        ]
    )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("batch_dl:"))
async def on_batch_download_all(callback: CallbackQuery, queue_mgr: QueueManager):
    batch_id = callback.data.split(":")[1]
    links = multi_link_batches.get(batch_id)

    if not links:
        await callback.answer("Batch expired or already processed.", show_alert=True)
        return

    user_id = callback.from_user.id
    enqueued_count = 0
    errors: List[str] = []

    await callback.message.edit_text("⏳ <b>Enqueueing all links...</b>", parse_mode="HTML")

    for idx, lnk in enumerate(links, 1):
        try:
            await queue_mgr.add_job(
                user_id=user_id,
                source_url=lnk.clean_url,
                filename=f"TeraBox_Batch_{idx}_{lnk.raw_surl[:6]}",
                file_size=0,
                category="other",
            )
            enqueued_count += 1
        except Exception as e:
            errors.append(str(e))
            break

    multi_link_batches.pop(batch_id, None)

    summary_text = (
        f"✅ <b>Batch Processing Started!</b>\n\n"
        f"• Successfully Enqueued: <code>{enqueued_count}/{len(links)}</code> jobs\n"
    )
    if errors:
        summary_text += f"⚠️ <i>Limit reached: {escape_html(errors[0])}</i>\n"

    summary_text += "\nCheck your active queue using /queue."
    await callback.message.edit_text(summary_text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("batch_cancel:"))
async def on_batch_cancel(callback: CallbackQuery):
    batch_id = callback.data.split(":")[1]
    multi_link_batches.pop(batch_id, None)
    await callback.message.edit_text("❌ Batch processing cancelled.", parse_mode="HTML")
    await callback.answer("Cancelled")
