import asyncio
from pathlib import Path
import pytest
from unittest.mock import MagicMock
from config.settings import settings
from database.database import get_session, init_db
from database.repositories import JobRepository, UserRepository
from services.queue_manager import QueueManager


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path):
    test_db = tmp_path / "test_queue.db"
    settings.DATABASE_URL = f"sqlite+aiosqlite:///{test_db.as_posix()}"
    asyncio.run(init_db())


@pytest.mark.asyncio
async def test_user_repository():
    async with get_session() as session:
        user = await UserRepository.get_or_create(session, telegram_id=12345, username="john_doe")
        assert user.telegram_id == 12345
        assert user.username == "john_doe"
        assert user.is_blocked is False

        # Block user
        await UserRepository.set_blocked(session, 12345, blocked=True)

    async with get_session() as session:
        updated = await UserRepository.get_user(session, 12345)
        assert updated.is_blocked is True


@pytest.mark.asyncio
async def test_queue_concurrency_and_cancellation():
    bot_mock = MagicMock()
    dm_mock = MagicMock()
    qm = QueueManager(bot=bot_mock, download_mgr=dm_mock)

    user_id = 998877

    # Enqueue job 1
    job1_id = await qm.add_job(
        user_id=user_id,
        source_url="https://terabox.com/s/1abc",
        filename="video1.mp4",
        file_size=1000,
        category="video",
    )
    assert job1_id is not None

    # Enqueue job 2
    job2_id = await qm.add_job(
        user_id=user_id,
        source_url="https://terabox.com/s/1def",
        filename="video2.mp4",
        file_size=2000,
        category="video",
    )
    assert job2_id is not None

    # Check active count = 2. Third job should raise limit error
    settings.MAX_USER_ACTIVE_JOBS = 2
    with pytest.raises(ValueError, match="maximum active downloads limit"):
        await qm.add_job(
            user_id=user_id,
            source_url="https://terabox.com/s/1ghi",
            filename="video3.mp4",
            file_size=3000,
        )

    # Cancel all user jobs
    cancelled_count = await qm.cancel_all_user_jobs(user_id)
    assert cancelled_count == 2
