import asyncio
import os
import psutil
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from bot.filters.admin_filter import AdminFilter
from bot.keyboards.admin_kb import get_admin_dashboard_keyboard
from config.settings import settings
from database.database import get_session
from database.repositories import JobRepository, UserRepository
from services.queue_manager import QueueManager
from services.storage_manager import StorageManager
from utils.helpers import escape_html, format_size
from utils.logger import logger

router = Router(name="admin_router")
# Apply AdminFilter to all handlers in this router
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


async def render_admin_dashboard_text(queue_mgr: QueueManager) -> str:
    async with get_session() as session:
        total_users = await UserRepository.count_users(session)
        job_stats = await JobRepository.count_by_status(session)
        total_data = await JobRepository.get_total_stats(session)

    storage_stats = StorageManager.get_storage_stats()

    # System metrics
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()

    completed_jobs = job_stats.get("completed", 0)
    failed_jobs = job_stats.get("failed", 0)
    active_jobs = job_stats.get("downloading", 0) + job_stats.get("uploading", 0)
    queued_jobs = job_stats.get("queued", 0)

    queue_status = "⏸ <b>PAUSED</b>" if queue_mgr.is_paused else "▶️ <b>RUNNING</b>"

    text = (
        f"👑 <b>TeraBox Admin Control Center</b>\n\n"
        f"⚙️ <b>Queue Engine:</b> {queue_status}\n\n"
        f"👥 <b>User Statistics:</b>\n"
        f"• Total Registered Users: <code>{total_users}</code>\n"
        f"• Data Transferred: <code>{format_size(total_data['total_bytes'])}</code>\n\n"
        f"📊 <b>Job Statistics:</b>\n"
        f"• Total Processed: <code>{total_data['total_jobs']}</code>\n"
        f"• Successful: <code>{completed_jobs}</code>\n"
        f"• Failed: <code>{failed_jobs}</code>\n"
        f"• Currently Active: <code>{active_jobs}</code>\n"
        f"• In Waiting Queue: <code>{queued_jobs}</code>\n\n"
        f"💻 <b>System Health:</b>\n"
        f"• CPU Usage: <code>{cpu_percent:.1f}%</code>\n"
        f"• Memory Usage: <code>{mem.percent:.1f}% ({format_size(mem.used)} / {format_size(mem.total)})</code>\n"
        f"• Disk Space: <code>{storage_stats['disk_free']} Free ({storage_stats['disk_percent']} used)</code>\n"
        f"• Bot Storage Cache: <code>{storage_stats['bot_storage']}</code>\n"
    )
    return text


@router.message(Command("admin"))
async def cmd_admin(message: Message, queue_mgr: QueueManager):
    """Open Admin Dashboard."""
    text = await render_admin_dashboard_text(queue_mgr)
    kb = get_admin_dashboard_keyboard(queue_mgr.is_paused)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "admin_refresh")
async def on_admin_refresh(callback: CallbackQuery, queue_mgr: QueueManager):
    text = await render_admin_dashboard_text(queue_mgr)
    kb = get_admin_dashboard_keyboard(queue_mgr.is_paused)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Stats refreshed.")


@router.callback_query(F.data == "admin_pause_queue")
async def on_admin_pause_queue(callback: CallbackQuery, queue_mgr: QueueManager):
    queue_mgr.is_paused = True
    logger.info(f"Admin {callback.from_user.id} paused the queue.")
    text = await render_admin_dashboard_text(queue_mgr)
    kb = get_admin_dashboard_keyboard(queue_mgr.is_paused)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Queue paused.")


@router.callback_query(F.data == "admin_resume_queue")
async def on_admin_resume_queue(callback: CallbackQuery, queue_mgr: QueueManager):
    queue_mgr.is_paused = False
    logger.info(f"Admin {callback.from_user.id} resumed the queue.")
    text = await render_admin_dashboard_text(queue_mgr)
    kb = get_admin_dashboard_keyboard(queue_mgr.is_paused)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Queue resumed.")


@router.callback_query(F.data == "admin_clean_storage")
async def on_admin_clean_storage(callback: CallbackQuery):
    await callback.answer("Cleaning storage cache...", show_alert=False)
    storage_mgr = StorageManager()
    count = await storage_mgr.cleanup_expired_files()
    await callback.answer(f"Storage clean complete. Deleted {count} files.", show_alert=True)


@router.callback_query(F.data == "admin_failed_jobs")
async def on_admin_failed_jobs(callback: CallbackQuery):
    async with get_session() as session:
        failed = await JobRepository.get_recent_failed_jobs(session, limit=5)

    if not failed:
        await callback.answer("No recent failed jobs.", show_alert=True)
        return

    text = "⚠️ <b>Recent Failed Jobs (Last 5):</b>\n\n"
    for j in failed:
        err = j.error_message[:40] if j.error_message else "Unknown error"
        text += (
            f"• <b>{escape_html(j.filename[:24])}</b>\n"
            f"  User: <code>{j.user_id}</code> | Size: <code>{format_size(j.file_size)}</code>\n"
            f"  Error: <i>{escape_html(err)}</i>\n\n"
        )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_close")
async def on_admin_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
async def on_admin_broadcast_prompt(callback: CallbackQuery):
    await callback.answer("Use command: /broadcast <your message>", show_alert=True)


@router.callback_query(F.data == "admin_manage_user")
async def on_admin_manage_user_prompt(callback: CallbackQuery):
    await callback.answer("Use commands: /block <user_id> or /unblock <user_id>", show_alert=True)


@router.message(Command("block"))
async def cmd_block_user(message: Message):
    """Block a user from using the bot: /block <telegram_id>"""
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: <code>/block &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return

    target_id = int(parts[1])
    async with get_session() as session:
        success = await UserRepository.set_blocked(session, target_id, blocked=True)

    if success:
        logger.info(f"Admin blocked user {target_id}")
        await message.answer(f"✅ User <code>{target_id}</code> has been blocked.", parse_mode="HTML")
    else:
        await message.answer(f"❌ User <code>{target_id}</code> not found in database.", parse_mode="HTML")


@router.message(Command("unblock"))
async def cmd_unblock_user(message: Message):
    """Unblock a user: /unblock <telegram_id>"""
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: <code>/unblock &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return

    target_id = int(parts[1])
    async with get_session() as session:
        success = await UserRepository.set_blocked(session, target_id, blocked=False)

    if success:
        logger.info(f"Admin unblocked user {target_id}")
        await message.answer(f"✅ User <code>{target_id}</code> has been unblocked.", parse_mode="HTML")
    else:
        await message.answer(f"❌ User <code>{target_id}</code> not found in database.", parse_mode="HTML")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    """Broadcast an announcement to all registered users: /broadcast <message>"""
    text = (message.text or "")[len("/broadcast ") :].strip()
    if not text:
        await message.answer("Usage: <code>/broadcast &lt;announcement text&gt;</code>", parse_mode="HTML")
        return

    async with get_session() as session:
        users = await UserRepository.get_all_users(session, limit=5000)

    await message.answer(f"📢 Starting broadcast to {len(users)} users...", parse_mode="HTML")

    success_count = 0
    fail_count = 0

    announcement = f"📢 <b>Announcement from Administration:</b>\n\n{escape_html(text)}"

    for u in users:
        try:
            await bot.send_message(chat_id=u.telegram_id, text=announcement, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.05)  # Telegram broadcast anti-flood throttle
        except Exception:
            fail_count += 1

    await message.answer(
        f"✅ <b>Broadcast Completed:</b>\n• Sent: <code>{success_count}</code>\n• Failed/Blocked: <code>{fail_count}</code>",
        parse_mode="HTML",
    )
