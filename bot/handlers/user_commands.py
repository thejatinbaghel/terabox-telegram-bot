from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from config.settings import settings
from database.database import get_session
from database.repositories import JobRepository, UserRepository
from bot.keyboards.settings_kb import get_settings_keyboard
from services.queue_manager import QueueManager
from services.storage_manager import StorageManager
from utils.helpers import escape_html, format_size, generate_progress_bar

router = Router(name="user_commands_router")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, queue_mgr: QueueManager):
    """Cancel all active or queued jobs for the user."""
    user_id = message.from_user.id if message.from_user else 0
    cancelled_count = await queue_mgr.cancel_all_user_jobs(user_id)
    if cancelled_count > 0:
        await message.answer(
            f"🛑 <b>Cancelled {cancelled_count} active/queued download job(s).</b>",
            parse_mode="HTML",
        )
    else:
        await message.answer("ℹ️ You have no active or pending downloads to cancel.")


@router.message(Command("queue"))
async def cmd_queue(message: Message):
    """View active and pending downloads for the user."""
    user_id = message.from_user.id if message.from_user else 0

    async with get_session() as session:
        user_jobs = await JobRepository.get_user_jobs(session, user_id, limit=10)

    active_jobs = [j for j in user_jobs if j.status in ("queued", "analyzing", "downloading", "packaging", "uploading")]

    if not active_jobs:
        await message.answer(
            "📭 <b>Your download queue is empty.</b>\nSend a TeraBox link to start downloading!",
            parse_mode="HTML",
        )
        return

    text = f"📋 <b>Your Download Queue ({len(active_jobs)} tasks):</b>\n\n"
    for idx, job in enumerate(active_jobs, 1):
        status_icon = {
            "queued": "⏳",
            "analyzing": "🔍",
            "downloading": "⬇️",
            "packaging": "📦",
            "uploading": "📤",
        }.get(job.status, "⚙️")

        size_str = format_size(job.file_size) if job.file_size > 0 else "Unknown"
        text += (
            f"<b>{idx}. {status_icon} {escape_html(job.filename[:28])}</b>\n"
            f"• Status: <code>{job.status.capitalize()}</code>\n"
            f"• Size: <code>{size_str}</code>\n"
        )
        if job.status == "downloading" and job.progress_pct > 0:
            bar = generate_progress_bar(job.progress_pct, length=8)
            text += f"• Progress: {bar}\n"
        text += "\n"

    text += "<i>To cancel all downloads, type /cancel</i>"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("history"))
async def cmd_history(message: Message):
    """Show user's recent download history."""
    user_id = message.from_user.id if message.from_user else 0

    async with get_session() as session:
        jobs = await JobRepository.get_user_jobs(session, user_id, limit=10)

    if not jobs:
        await message.answer("📜 <b>You have no download history yet.</b>", parse_mode="HTML")
        return

    text = "📜 <b>Recent Downloads (Last 10):</b>\n\n"
    for idx, job in enumerate(jobs, 1):
        status_symbol = "✅" if job.status == "completed" else "❌" if job.status == "failed" else "⏳"
        size_str = format_size(job.file_size) if job.file_size > 0 else "N/A"
        date_str = job.created_at.strftime("%b %d, %H:%M")

        text += (
            f"{idx}. {status_symbol} <b>{escape_html(job.filename[:26])}</b>\n"
            f"   Size: <code>{size_str}</code> | Date: <code>{date_str}</code>\n"
            f"   Status: <i>{job.status.capitalize()}</i>\n\n"
        )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    """Display user preferences."""
    user_id = message.from_user.id if message.from_user else 0
    current_delivery = "telegram"

    async with get_session() as session:
        user = await UserRepository.get_user(session, user_id)
        if user:
            current_delivery = user.preferred_delivery

    text = (
        f"⚙️ <b>User Preferences</b>\n\n"
        f"Select your preferred default delivery method for files:\n"
        f"• <b>Direct Telegram Delivery:</b> Files are sent straight into this chat (up to {settings.MAX_TG_UPLOAD_SIZE_MB}MB).\n"
        f"• <b>Temporary Download Link:</b> Generates a private, high-speed HTTP link for external download managers.\n"
    )

    kb = get_settings_keyboard(current_delivery)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("set_delivery:"))
async def on_set_delivery_preference(callback: CallbackQuery):
    delivery_method = callback.data.split(":")[1]
    user_id = callback.from_user.id

    async with get_session() as session:
        await UserRepository.set_preferred_delivery(session, user_id, delivery_method)

    await callback.answer(f"Delivery set to {delivery_method.replace('_', ' ').title()}")
    kb = get_settings_keyboard(delivery_method)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "close_settings")
async def on_close_settings(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Current system and user limits status."""
    user_id = message.from_user.id if message.from_user else 0

    async with get_session() as session:
        active_count = await JobRepository.count_user_active_jobs(session, user_id)
        total_counts = await JobRepository.count_by_status(session)

    storage_stats = StorageManager.get_storage_stats()
    limit_str = str(settings.MAX_USER_ACTIVE_JOBS) if settings.MAX_USER_ACTIVE_JOBS > 0 else "Unlimited"

    text = (
        f"📊 <b>System & Account Status</b>\n\n"
        f"👤 <b>Your Limits:</b>\n"
        f"• Active Jobs: <code>{active_count} / {limit_str}</code>\n"
        f"• Max File Size: <code>{settings.MAX_FILE_SIZE_MB} MB</code>\n"
        f"• Direct TG Limit: <code>{settings.MAX_TG_UPLOAD_SIZE_MB} MB</code>\n\n"
        f"🖥 <b>Server Status:</b>\n"
        f"• Global Active: <code>{total_counts.get('downloading', 0) + total_counts.get('uploading', 0)}</code>\n"
        f"• Global Queued: <code>{total_counts.get('queued', 0)}</code>\n"
        f"• Free Disk Space: <code>{storage_stats['disk_free']} ({storage_stats['disk_percent']} used)</code>\n"
        f"• Temp Link Expiry: <code>{settings.TEMP_LINK_EXPIRY_HOURS} hours</code>"
    )
    await message.answer(text, parse_mode="HTML")
