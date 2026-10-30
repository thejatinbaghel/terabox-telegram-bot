import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from config.settings import settings
from database.database import get_session, init_db
from database.repositories import JobRepository, TempLinkRepository
from services.storage_manager import StorageManager
from services.temporary_link_server import TemporaryLinkServer


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path):
    test_db = tmp_path / "test_storage.db"
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{test_db.as_posix()}"
    settings.DOWNLOAD_DIR = tmp_path / "downloads"
    settings.TEMP_DIR = tmp_path / "temp"
    settings.ARCHIVE_DIR = tmp_path / "archives"
    settings.ensure_directories()
    asyncio.run(init_db())


@pytest.mark.asyncio
async def test_temp_link_creation_and_expiry(tmp_path: Path):
    test_file = settings.DOWNLOAD_DIR / "sample.mp4"
    test_file.write_bytes(b"A" * 1024)

    url, expires_at = await TemporaryLinkServer.create_download_link(
        user_id=123,
        file_path=test_file,
        filename="sample.mp4",
        expiry_hours=6,
    )
    assert "/d/" in url
    token = url.split("/d/")[1]
    assert len(token) > 20

    async with get_session() as session:
        link = await TempLinkRepository.get_link(session, token)
        assert link is not None
        assert link.filename == "sample.mp4"
        assert not link.is_expired()


@pytest.mark.asyncio
async def test_storage_cleanup_preserves_active_files(tmp_path: Path):
    # Create an active job file
    active_file = settings.DOWNLOAD_DIR / "active.mp4"
    active_file.write_bytes(b"active content")

    async with get_session() as session:
        # Register user and active job
        job = await JobRepository.create_job(
            session=session,
            user_id=111,
            source_url="https://terabox.com/s/1active",
            filename="active.mp4",
        )
        # Mark local path as active
        await JobRepository.update_status(session, job.id, status="downloading", local_path=str(active_file))

    # Create an expired temp link file
    expired_file = settings.DOWNLOAD_DIR / "expired.mp4"
    expired_file.write_bytes(b"expired content")

    async with get_session() as session:
        await TempLinkRepository.create_link(
            session=session,
            token="expired_token_123",
            user_id=111,
            file_path=str(expired_file.resolve()),
            filename="expired.mp4",
            file_size=15,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Expired 1 hour ago
        )

    storage_mgr = StorageManager()
    deleted_count = await storage_mgr.cleanup_expired_files()

    assert deleted_count == 1
    assert not expired_file.exists()  # Expired file must be deleted
    assert active_file.exists()  # Active job file must strictly NOT be deleted
