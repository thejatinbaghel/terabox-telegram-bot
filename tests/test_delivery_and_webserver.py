from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from httpx import ASGITransport, AsyncClient
import pytest
from aiogram.exceptions import TelegramAPIError
from config.settings import settings
from database.database import get_session, init_db
from database.repositories import TempLinkRepository
from services.content_detector import FileCategory
from services.telegram_sender import TelegramSender
from services.temporary_link_server import TemporaryLinkServer, app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path):
    test_db = tmp_path / "test_delivery.db"
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{test_db.as_posix()}"
    settings.DOWNLOAD_DIR = tmp_path / "downloads"
    settings.TEMP_DIR = tmp_path / "temp"
    settings.ARCHIVE_DIR = tmp_path / "archives"
    settings.ensure_directories()
    import asyncio
    asyncio.run(init_db())


@pytest.mark.asyncio
async def test_telegram_sender_oversized_file_fallback():
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    sender = TelegramSender(bot=bot_mock)
    settings.MAX_TG_UPLOAD_SIZE_MB = 10  # 10 MB limit

    # Create dummy 15MB file
    oversized = settings.DOWNLOAD_DIR / "large_movie.mp4"
    oversized.write_bytes(b"0" * (15 * 1024 * 1024))

    assert sender.is_file_within_tg_limit(oversized) is False

    # Delivering oversized file must automatically trigger deliver_via_temp_link
    success = await sender.deliver_file(
        chat_id=12345,
        file_path=oversized,
        filename="large_movie.mp4",
        category=FileCategory.VIDEO,
    )
    assert success is True
    # send_message should have been called with temporary download link
    bot_mock.send_message.assert_called_once()
    call_args = bot_mock.send_message.call_args[1]
    assert "Private Download Link Ready" in call_args["text"]


@pytest.mark.asyncio
async def test_temporary_link_http_server_range_requests():
    # Setup test file
    sample_file = settings.DOWNLOAD_DIR / "sample_video.mp4"
    file_bytes = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 36 bytes
    sample_file.write_bytes(file_bytes)

    # Register temporary link
    url, expires = await TemporaryLinkServer.create_download_link(
        user_id=999,
        file_path=sample_file,
        filename="sample_video.mp4",
        expiry_hours=6,
    )
    token = url.split("/d/")[1]

    # Test through FastAPI test client with Range request
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Full content request
        resp_full = await client.get(f"/d/{token}")
        assert resp_full.status_code == 200
        assert resp_full.content == file_bytes

        # Range request: bytes=0-9 (first 10 bytes)
        headers = {"Range": "bytes=0-9"}
        resp_range = await client.get(f"/d/{token}", headers=headers)
        assert resp_range.status_code == 206
        assert resp_range.content == b"0123456789"
        assert resp_range.headers["Content-Range"] == "bytes 0-9/36"

        # Range request: bytes=10- (from byte 10 to end)
        headers_resume = {"Range": "bytes=10-"}
        resp_resume = await client.get(f"/d/{token}", headers=headers_resume)
        assert resp_resume.status_code == 206
        assert resp_resume.content == file_bytes[10:]
