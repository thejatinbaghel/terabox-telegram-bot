import asyncio
import os
from pathlib import Path
import shutil
import time
from typing import Dict, List, Set
from config.settings import settings
from database.database import get_session
from database.repositories import JobRepository, TempLinkRepository
from utils.helpers import format_size
from utils.logger import logger


class StorageManager:
    """Monitors storage usage and automatically cleans expired files and temp data."""

    def __init__(self, cleanup_interval_minutes: int = 15):
        self.cleanup_interval = cleanup_interval_minutes * 60
        self._is_running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._cleanup_loop())
            logger.info("StorageManager background cleanup loop started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StorageManager stopped.")

    @staticmethod
    def get_storage_stats() -> Dict[str, str]:
        """Return formatted disk usage and bot storage statistics."""
        try:
            total, used, free = shutil.disk_usage(settings.DOWNLOAD_DIR)
            pct = (used / total) * 100 if total > 0 else 0

            # Calculate total bot storage usage
            bot_bytes = 0
            for d in [settings.DOWNLOAD_DIR, settings.TEMP_DIR, settings.ARCHIVE_DIR]:
                if d.exists():
                    for root, _, files in os.walk(d):
                        for f in files:
                            try:
                                bot_bytes += (Path(root) / f).stat().st_size
                            except Exception:
                                pass

            return {
                "disk_total": format_size(total),
                "disk_used": format_size(used),
                "disk_free": format_size(free),
                "disk_percent": f"{pct:.1f}%",
                "bot_storage": format_size(bot_bytes),
            }
        except Exception as e:
            logger.error(f"Error fetching storage stats: {e}")
            return {
                "disk_total": "N/A",
                "disk_used": "N/A",
                "disk_free": "N/A",
                "disk_percent": "N/A",
                "bot_storage": "N/A",
            }

    async def cleanup_expired_files(self) -> int:
        """
        Delete expired temporary download files, old archives, and abandoned .part files.
        Guarantees that files belonging to active jobs are never touched.
        """
        deleted_count = 0
        try:
            # 1. Identify active job files to protect them
            active_paths: Set[str] = set()
            async with get_session() as session:
                active_jobs = await JobRepository.get_active_jobs(session)
                for job in active_jobs:
                    if job.local_path:
                        active_paths.add(str(Path(job.local_path).resolve()))

            # 2. Clean expired TempDownloadLink entries
            async with get_session() as session:
                expired_links = await TempLinkRepository.get_expired_links(session)
                for link in expired_links:
                    path = Path(link.file_path)
                    if path.resolve() not in active_paths and path.exists() and path.is_file():
                        try:
                            path.unlink()
                            deleted_count += 1
                            logger.info(f"Cleaned expired temporary link file: {path.name}")
                        except Exception as e:
                            logger.warning(f"Could not delete expired file {path}: {e}")
                    await TempLinkRepository.delete_link(session, link.token)

            # 3. Clean abandoned .part files older than 6 hours
            now = time.time()
            six_hours_ago = now - (6 * 3600)
            for directory in [settings.DOWNLOAD_DIR, settings.TEMP_DIR]:
                if not directory.exists():
                    continue
                for item in directory.glob("*.part"):
                    if str(item.resolve()) in active_paths:
                        continue
                    try:
                        if item.stat().st_mtime < six_hours_ago:
                            item.unlink()
                            deleted_count += 1
                            logger.info(f"Cleaned abandoned partial file: {item.name}")
                    except Exception as e:
                        logger.warning(f"Error deleting part file {item}: {e}")

            # 4. Clean temporary archives older than 6 hours
            if settings.ARCHIVE_DIR.exists():
                for item in settings.ARCHIVE_DIR.glob("*.zip"):
                    if str(item.resolve()) in active_paths:
                        continue
                    try:
                        if item.stat().st_mtime < six_hours_ago:
                            item.unlink()
                            deleted_count += 1
                            logger.info(f"Cleaned old archive: {item.name}")
                    except Exception as e:
                        logger.warning(f"Error deleting old archive {item}: {e}")

        except Exception as e:
            logger.error(f"Error during storage cleanup: {e}")

        return deleted_count

    async def _cleanup_loop(self) -> None:
        while self._is_running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                logger.info("Running scheduled storage cleanup...")
                count = await self.cleanup_expired_files()
                logger.info(f"Storage cleanup completed. Deleted {count} expired files.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in storage cleanup loop: {e}")
