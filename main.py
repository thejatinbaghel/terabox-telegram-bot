import asyncio
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from admin.dashboard import router as admin_router
from bot.handlers.browser import router as browser_router
from bot.handlers.links import router as links_router
from bot.handlers.start import router as start_router
from bot.handlers.user_commands import router as user_commands_router
from bot.middlewares.user_middleware import ThrottlingMiddleware, UserMiddleware
from config.settings import settings
from database.database import init_db
from services.download_manager import DownloadManager
from services.queue_manager import QueueManager
from services.storage_manager import StorageManager
from services.temporary_link_server import TemporaryLinkServer
from utils.logger import logger


async def main() -> None:
    logger.info("Starting Ultimate TeraBox Downloader Bot...")

    # Ensure required directories exist
    settings.ensure_directories()

    # Initialize database
    await init_db()

    # Configure custom local Telegram Bot API Server if specified
    session = None
    if settings.LOCAL_BOT_API_SERVER_URL:
        logger.info(f"Using local Telegram Bot API server: {settings.LOCAL_BOT_API_SERVER_URL}")
        api_server = TelegramAPIServer.from_base(settings.LOCAL_BOT_API_SERVER_URL, is_local=True)
        session = AiohttpSession(api=api_server)

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    # Initialize Core Services
    download_mgr = DownloadManager(max_concurrent=settings.MAX_CONCURRENT_DOWNLOADS)
    queue_mgr = QueueManager(bot=bot, download_mgr=download_mgr)
    storage_mgr = StorageManager(cleanup_interval_minutes=settings.CLEANUP_INTERVAL_MINUTES)

    # Register Middlewares
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.5))
    dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.5))

    # Register Routers
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(user_commands_router)
    dp.include_router(browser_router)
    dp.include_router(links_router)

    # Start Background Workers
    queue_mgr.start()
    storage_mgr.start()

    # Start Temporary Download Link HTTP Server in Background Task
    temp_server_task = asyncio.create_task(TemporaryLinkServer.start_server())

    # Graceful Shutdown Handler
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received. Initiating graceful shutdown...")
        stop_event.set()

    # Attach signals where supported
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except (NotImplementedError, RuntimeError):
            # Windows signal handling fallback
            pass

    logger.info("Bot successfully initialized and listening for updates.")

    try:
        # Pass queue_mgr to handlers via dependency injection
        polling_task = asyncio.create_task(
            dp.start_polling(bot, queue_mgr=queue_mgr, allowed_updates=dp.resolve_used_update_types())
        )

        # Wait until shutdown signal or cancellation
        done, pending = await asyncio.wait(
            [polling_task, temp_server_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown initiated by user.")

    finally:
        logger.info("Stopping all services...")
        await queue_mgr.stop()
        await storage_mgr.stop()
        temp_server_task.cancel()
        try:
            await temp_server_task
        except asyncio.CancelledError:
            pass

        await bot.session.close()
        logger.info("All services safely shut down. Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
