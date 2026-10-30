import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from database.database import get_session
from database.repositories import UserRepository
from utils.logger import logger


class UserMiddleware(BaseMiddleware):
    """
    Middleware that automatically records users into the database and
    blocks requests from blocked users.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        async with get_session() as session:
            db_user = await UserRepository.get_or_create(
                session=session,
                telegram_id=user.id,
                username=user.username,
                full_name=user.full_name,
            )

            if db_user.is_blocked:
                logger.warning(f"Blocked user {user.id} attempted to interact with bot.")
                if isinstance(event, Message):
                    await event.answer("🚫 <b>Access Denied:</b> Your account is currently suspended by administration.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Account suspended.", show_alert=True)
                return None

            data["db_user"] = db_user

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Rate limiting middleware to prevent user spamming."""

    def __init__(self, rate_limit: float = 0.8):
        super().__init__()
        self.rate_limit = rate_limit
        self._user_last_action: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        now = time.time()
        last_action = self._user_last_action.get(user.id, 0.0)

        if now - last_action < self.rate_limit:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Please slow down...", show_alert=False)
            return None

        self._user_last_action[user.id] = now
        return await handler(event, data)
