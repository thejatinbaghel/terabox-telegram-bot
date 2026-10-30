from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_settings_keyboard(current_delivery: str) -> InlineKeyboardMarkup:
    tg_check = "✅ " if current_delivery == "telegram" else ""
    link_check = "✅ " if current_delivery == "direct_link" else ""

    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{tg_check}📱 Direct Telegram Delivery",
                callback_data="set_delivery:telegram",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{link_check}🔗 Temporary Download Link",
                callback_data="set_delivery:direct_link",
            )
        ],
        [InlineKeyboardButton(text="❌ Close", callback_data="close_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
