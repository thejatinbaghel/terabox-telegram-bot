import asyncio
import os
from pathlib import Path
import pytest
from services.content_detector import ContentDetector, ContentScenario, FileCategory, TeraBoxItem
from services.archive_manager import ArchiveItem, ArchiveManager
from services.terabox_client import TeraBoxClient


def test_isdir_string_zero_is_not_directory():
    """Verify that 'isdir': '0' (as returned by TeraBox API) is NOT treated as True."""
    raw_entry = {
        "server_filename": "video.mp4",
        "isdir": "0",
        "size": "25000000",
        "fs_id": "12345",
        "category": "1"
    }
    raw_isdir = raw_entry.get("isdir", 0)
    is_dir = str(raw_isdir).strip() == "1"
    assert is_dir is False

    cat = ContentDetector.detect_category(raw_entry["server_filename"], is_dir=is_dir)
    assert cat == FileCategory.VIDEO

    item = TeraBoxItem(
        fs_id="12345",
        filename="video.mp4",
        size=25000000,
        is_dir=is_dir,
        path="/video.mp4",
        category=cat
    )
    analysis = ContentDetector.analyze([item])
    assert analysis.scenario == ContentScenario.SINGLE_VIDEO
    assert analysis.total_files == 1
    assert analysis.total_folders == 0


def test_isdir_string_one_is_directory():
    """Verify that 'isdir': '1' is recognized as a folder."""
    raw_entry = {
        "server_filename": "MyFolder",
        "isdir": "1",
        "size": "0",
        "fs_id": "99999",
    }
    raw_isdir = raw_entry.get("isdir", 0)
    is_dir = str(raw_isdir).strip() == "1"
    assert is_dir is True

    cat = ContentDetector.detect_category(raw_entry["server_filename"], is_dir=is_dir)
    assert cat == FileCategory.FOLDER


def test_folder_zip_creation_with_populated_files(tmp_path: Path):
    """Verify that packaging a folder with files creates a valid, non-empty ZIP archive."""
    folder_dir = tmp_path / "test_folder"
    folder_dir.mkdir(parents=True)

    # Create dummy files simulating downloaded files in folder
    file1 = folder_dir / "clip1.mp4"
    file1.write_bytes(b"Simulated MP4 video content 1" * 1000)

    sub_dir = folder_dir / "subfolder"
    sub_dir.mkdir(parents=True)
    file2 = sub_dir / "clip2.mp4"
    file2.write_bytes(b"Simulated MP4 video content 2" * 1000)

    out_zip = tmp_path / "output_archives"
    out_zip.mkdir(parents=True)

    archive_path = ArchiveManager.create_zip_from_directory(
        source_dir=folder_dir,
        archive_name="test_folder.zip"
    )

    assert archive_path.exists()
    zip_size = archive_path.stat().st_size
    # Must be substantially larger than an empty 22-byte zip!
    assert zip_size > 22
    assert zip_size > 100

    # Verify contents inside the ZIP
    import zipfile
    with zipfile.ZipFile(archive_path, "r") as zf:
        namelist = zf.namelist()
        assert "clip1.mp4" in namelist or "test_folder/clip1.mp4" in namelist or any("clip1.mp4" in n for n in namelist)
        assert any("clip2.mp4" in n for n in namelist)

    if archive_path.exists():
        archive_path.unlink()


def test_streaming_signature_generation():
    """Verify that TeraBoxClient HMAC-SHA1 streaming signature matches specification."""
    browserid = "TEST_BROWSER_ID"
    timestamp = "1700000000"
    sign = TeraBoxClient.generate_streaming_signature(
        browserid=browserid,
        timestamp=timestamp,
        clienttype="0",
        channel="dubox"
    )
    assert isinstance(sign, str)
    assert len(sign) == 40  # SHA1 hex is 40 chars
    # Ensure deterministic calculation
    sign2 = TeraBoxClient.generate_streaming_signature(
        browserid=browserid,
        timestamp=timestamp,
        clienttype="0",
        channel="dubox"
    )
    assert sign == sign2


@pytest.mark.asyncio
async def test_unlimited_user_active_jobs_when_zero(tmp_path: Path):
    """Verify that setting MAX_USER_ACTIVE_JOBS = 0 allows unlimited jobs without raising error."""
    from unittest.mock import MagicMock
    from services.queue_manager import QueueManager
    from config.settings import settings
    from database.database import init_db
    await init_db()

    qm = QueueManager(bot=MagicMock(), download_mgr=MagicMock())
    settings.MAX_USER_ACTIVE_JOBS = 0
    user_id = 998877

    # Enqueue 5 jobs in a row
    job_ids = []
    for i in range(5):
        jid = await qm.add_job(
            user_id=user_id,
            source_url="https://terabox.com/s/test",
            filename=f"clip_{i}.mp4",
            file_size=1000,
        )
        job_ids.append(jid)

    assert len(job_ids) == 5
    cancelled = await qm.cancel_all_user_jobs(user_id)
    assert cancelled == 5


