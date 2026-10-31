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


@pytest.mark.asyncio
async def test_auto_unwrap_single_folder_returns_content():
    """Verify that a single folder is automatically unwrapped to return its content directly."""
    from unittest.mock import AsyncMock
    from services.terabox_client import ShareInfoResult

    client = TeraBoxClient()
    folder_item = TeraBoxItem(
        fs_id="1", filename="MyMovie", size=0, is_dir=True, path="/MyMovie", category=FileCategory.FOLDER
    )
    video_item = TeraBoxItem(
        fs_id="2", filename="movie.mp4", size=50000000, is_dir=False, path="/MyMovie/movie.mp4", category=FileCategory.VIDEO
    )

    async def mock_get_share_info(link, password=None, dir_path="/", page=1, page_size=100):
        if dir_path == "/":
            return ShareInfoResult(success=True, errno=0, errmsg="", items=[folder_item], current_dir="/")
        elif dir_path == "/MyMovie":
            return ShareInfoResult(success=True, errno=0, errmsg="", items=[video_item], current_dir="/MyMovie")
        return ShareInfoResult(success=False, errno=-1, errmsg="not found", items=[])

    client.get_share_info = AsyncMock(side_effect=mock_get_share_info)

    unwrapped = await client.get_content_unwrapped("https://terabox.app/s/test")
    assert unwrapped.success is True
    assert len(unwrapped.items) == 1
    assert unwrapped.items[0].filename == "movie.mp4"
    assert unwrapped.items[0].is_dir is False
    assert unwrapped.current_dir == "/MyMovie"


@pytest.mark.asyncio
async def test_auto_unwrap_nested_single_folders():
    """Verify that multiple nested single folders unwrap down to the files."""
    from unittest.mock import AsyncMock
    from services.terabox_client import ShareInfoResult

    client = TeraBoxClient()
    folder_a = TeraBoxItem(fs_id="1", filename="A", size=0, is_dir=True, path="/A", category=FileCategory.FOLDER)
    folder_b = TeraBoxItem(fs_id="2", filename="B", size=0, is_dir=True, path="/A/B", category=FileCategory.FOLDER)
    image_item = TeraBoxItem(fs_id="3", filename="pic.jpg", size=12000, is_dir=False, path="/A/B/pic.jpg", category=FileCategory.IMAGE)

    async def mock_get_share_info(link, password=None, dir_path="/", page=1, page_size=100):
        if dir_path == "/":
            return ShareInfoResult(success=True, errno=0, errmsg="", items=[folder_a], current_dir="/")
        elif dir_path == "/A":
            return ShareInfoResult(success=True, errno=0, errmsg="", items=[folder_b], current_dir="/A")
        elif dir_path == "/A/B":
            return ShareInfoResult(success=True, errno=0, errmsg="", items=[image_item], current_dir="/A/B")
        return ShareInfoResult(success=False, errno=-1, errmsg="not found", items=[])

    client.get_share_info = AsyncMock(side_effect=mock_get_share_info)

    unwrapped = await client.get_content_unwrapped("https://terabox.app/s/test")
    assert unwrapped.success is True
    assert len(unwrapped.items) == 1
    assert unwrapped.items[0].filename == "pic.jpg"
    assert unwrapped.current_dir == "/A/B"


@pytest.mark.asyncio
async def test_multiple_folders_are_not_unwrapped():
    """Verify that if there are multiple folders, they are preserved and shown."""
    from unittest.mock import AsyncMock
    from services.terabox_client import ShareInfoResult

    client = TeraBoxClient()
    folder_1 = TeraBoxItem(fs_id="1", filename="Folder1", size=0, is_dir=True, path="/Folder1", category=FileCategory.FOLDER)
    folder_2 = TeraBoxItem(fs_id="2", filename="Folder2", size=0, is_dir=True, path="/Folder2", category=FileCategory.FOLDER)

    async def mock_get_share_info(link, password=None, dir_path="/", page=1, page_size=100):
        return ShareInfoResult(success=True, errno=0, errmsg="", items=[folder_1, folder_2], current_dir="/")

    client.get_share_info = AsyncMock(side_effect=mock_get_share_info)

    unwrapped = await client.get_content_unwrapped("https://terabox.app/s/test")
    assert unwrapped.success is True
    assert len(unwrapped.items) == 2
    assert all(it.is_dir for it in unwrapped.items)
    assert unwrapped.current_dir == "/"


