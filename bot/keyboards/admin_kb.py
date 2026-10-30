from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_admin_dashboard_keyboard(is_queue_paused: bool) -> InlineKeyboardMarkup:
    pause_text = "▶️ Resume Queue" if is_queue_paused else "⏸ Pause Queue"
    pause_action = "admin_resume_queue" if is_queue_paused else "admin_pause_queue"

    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Refresh Stats", callback_data="admin_refresh"),
            InlineKeyboardButton(text=pause_text, callback_data=pause_action),
        ],
        [
            InlineKeyboardButton(text="🧹 Clean Storage", callback_data="admin_clean_storage"),
            InlineKeyboardButton(text="⚠️ Failed Jobs", callback_data="admin_failed_jobs"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin_broadcast"),
            InlineKeyboardButton(text="👤 Manage User", callback_data="admin_manage_user"),
        ],
        [InlineKeyboardButton(text="❌ Close Panel", callback_data="admin_close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
