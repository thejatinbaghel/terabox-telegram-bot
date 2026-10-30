import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from services.download_manager import DownloadCancelledException, DownloadManager, DownloadProgress


@pytest.mark.asyncio
async def test_download_cancellation():
    dm = DownloadManager()
    job_id = "test-job-cancel"

    cancel_event = dm.get_cancel_event(job_id)
    assert not cancel_event.is_set()

    # Trigger cancellation
    success = dm.cancel_job(job_id)
    assert success is True
    assert cancel_event.is_set()


@pytest.mark.asyncio
async def test_download_progress_calculation():
    prog = DownloadProgress(
        downloaded_bytes=500,
        total_bytes=1000,
        speed_bps=100.0,
        eta_seconds=5,
        percentage=50.0,
    )
    assert prog.percentage == 50.0
    assert prog.eta_seconds == 5
    assert prog.downloaded_bytes == 500


@pytest.mark.asyncio
async def test_download_cancellation_during_run(tmp_path: Path):
    dm = DownloadManager()
    job_id = "test-active-cancel"
    dm.cancel_job(job_id)  # Pre-cancel

    with pytest.raises(DownloadCancelledException):
        await dm.download_file(
            job_id=job_id,
            url="https://example.com/test.mp4",
            filename="test.mp4",
            destination_dir=tmp_path,
        )
