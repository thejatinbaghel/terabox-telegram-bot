from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from config.settings import settings


class AdminFilter(BaseFilter):
    """Filter to restrict handler execution strictly to configured ADMIN_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id if event.from_user else 0
        return user_id in settings.ADMIN_IDS
