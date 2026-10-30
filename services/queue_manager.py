import asyncio
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from aiogram import Bot
from config.settings import settings
from database.database import get_session
from database.repositories import JobRepository, UserRepository
from database.models import DownloadJob
from services.archive_manager import ArchiveItem, ArchiveManager
from services.content_detector import ContentDetector, FileCategory, TeraBoxItem
from services.download_manager import DownloadCancelledException, DownloadManager, DownloadProgress
from services.telegram_sender import TelegramSender
from services.terabox_client import TeraBoxClient
from utils.helpers import escape_html, format_seconds, format_size, format_speed, generate_progress_bar
from utils.logger import logger


@dataclass
class ActiveDownloadTask:
    job_id: str
    user_id: int
    filename: str
    telegram_message_id: Optional[int] = None
    last_ui_update: float = 0.0


class QueueManager:
    """Coordinates download queues, enforces user concurrency, and runs the worker loop."""

    def __init__(self, bot: Bot, download_mgr: DownloadManager):
        self.bot = bot
        self.download_mgr = download_mgr
        self.sender = TelegramSender(bot)
        self.terabox_client = TeraBoxClient()
        self._is_running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._active_tasks: Dict[str, ActiveDownloadTask] = {}
        self.is_paused = False

    def start(self) -> None:
        if not self._is_running:
            self._is_running = True
            self._worker_task = asyncio.create_task(self._queue_worker_loop())
            logger.info("QueueManager worker loop started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("QueueManager worker loop stopped.")

    async def add_job(
        self,
        user_id: int,
        source_url: str,
        filename: str,
        file_size: int = 0,
        fs_id: Optional[str] = None,
        category: str = "other",
        delivery_method: str = "telegram",
        dir_path: str = "/",
    ) -> str:
        """Enqueue a new download job. Returns job_id."""
        async with get_session() as session:
            # Check user active jobs limit (only enforced if MAX_USER_ACTIVE_JOBS > 0)
            if settings.MAX_USER_ACTIVE_JOBS > 0:
                active_count = await JobRepository.count_user_active_jobs(session, user_id)
                if active_count >= settings.MAX_USER_ACTIVE_JOBS:
                    raise ValueError(
                        f"You have reached the maximum active downloads limit ({settings.MAX_USER_ACTIVE_JOBS}). "
                        "Please wait for current downloads to finish."
                    )

            # Check global queue limit
            status_counts = await JobRepository.count_by_status(session)
            queued_count = status_counts.get("queued", 0)
            if queued_count >= settings.MAX_QUEUE_SIZE:
                raise ValueError("Server queue is currently full. Please try again in a few minutes.")

            job = await JobRepository.create_job(
                session=session,
                user_id=user_id,
                source_url=source_url,
                filename=filename,
                file_size=file_size,
                fs_id=fs_id,
                category=category,
                delivery_method=delivery_method,
                dir_path=dir_path,
            )
            job_id = job.id

        logger.info(f"Enqueued job {job_id} for user {user_id} ({filename}) [dir={dir_path}]")
        return job_id

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel an individual job."""
        self.download_mgr.cancel_job(job_id)
        async with get_session() as session:
            job = await JobRepository.get_job(session, job_id)
            if job and job.status in ("queued", "analyzing", "downloading", "packaging", "uploading"):
                await JobRepository.update_status(session, job_id, status="cancelled")
                self._active_tasks.pop(job_id, None)
                logger.info(f"Cancelled job {job_id}")
                return True
        return False

    async def cancel_all_user_jobs(self, user_id: int) -> int:
        """Cancel all active and queued jobs for a user."""
        # Cancel any active downloads running in memory
        for jid, task in list(self._active_tasks.items()):
            if task.user_id == user_id:
                self.download_mgr.cancel_job(jid)

        async with get_session() as session:
            cancelled_count = await JobRepository.cancel_user_jobs(session, user_id)
        logger.info(f"Cancelled {cancelled_count} jobs for user {user_id}")
        return cancelled_count

    async def get_queue_position(self, job_id: str) -> int:
        """Calculate the 1-based position of a job in the waiting queue."""
        async with get_session() as session:
            queued = await JobRepository.get_queued_jobs(session, limit=100)
            for idx, j in enumerate(queued, 1):
                if j.id == job_id:
                    return idx
        return 0

    async def _queue_worker_loop(self) -> None:
        """Continuously pulls queued jobs and processes them."""
        while self._is_running:
            try:
                if self.is_paused:
                    await asyncio.sleep(3)
                    continue

                async with get_session() as session:
                    queued_jobs = await JobRepository.get_queued_jobs(session, limit=5)

                if not queued_jobs:
                    await asyncio.sleep(2)
                    continue

                for job in queued_jobs:
                    if not self._is_running or self.is_paused:
                        break

                    # Dispatch job processing asynchronously without blocking queue worker
                    asyncio.create_task(self._process_single_job(job.id))
                    # Stagger starts slightly to avoid instant burst
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Error in queue worker loop: {e}")
                await asyncio.sleep(3)

    async def _process_single_job(self, job_id: str) -> None:
        """Full pipeline for a single download job: Analyze -> Download -> Package -> Deliver."""
        msg_id: Optional[int] = None
        user_id = 0
        filename = "file"

        try:
            async with get_session() as session:
                job = await JobRepository.get_job(session, job_id)
                if not job or job.status != "queued":
                    return
                user_id = job.user_id
                filename = job.filename
                source_url = job.source_url
                fs_id = job.fs_id
                file_size = job.file_size
                cat_str = job.category
                delivery_method = job.delivery_method
                dir_path = getattr(job, "dir_path", "/") or "/"

                # Update status to Analyzing
                await JobRepository.update_status(session, job_id, status="analyzing")

            task = ActiveDownloadTask(job_id=job_id, user_id=user_id, filename=filename)
            self._active_tasks[job_id] = task

            # Send initial progress message to Telegram user
            try:
                prog_msg = await self.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⏳ <b>Download Initialized</b>\n\n"
                        f"📄 <b>File:</b> <code>{escape_html(filename)}</code>\n"
                        f"📊 <b>Status:</b> Analyzing link..."
                    ),
                    parse_mode="HTML",
                )
                task.telegram_message_id = prog_msg.message_id
                msg_id = prog_msg.message_id
            except Exception as e:
                logger.warning(f"Could not send initial progress message to {user_id}: {e}")

            # Check if this is a folder download
            is_folder_job = cat_str in ("folder", "archive") or filename.lower().endswith(".zip")
            if is_folder_job and (not fs_id or cat_str == "folder"):
                await self._process_folder_download(job, task, msg_id)
            else:
                await self._process_file_download(job, task, msg_id, dir_path)

        except DownloadCancelledException:
            logger.info(f"Job {job_id} was successfully cancelled.")
            async with get_session() as session:
                await JobRepository.update_status(session, job_id, status="cancelled")
            if msg_id and user_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=f"❌ Download cancelled for <code>{escape_html(filename)}</code>.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error processing job {job_id}: {e}", exc_info=True)
            async with get_session() as session:
                await JobRepository.update_status(session, job_id, status="failed", error_message=str(e))
            if msg_id and user_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=(
                            f"❌ <b>Download Failed</b>\n\n"
                            f"📄 <b>File:</b> <code>{escape_html(filename)}</code>\n"
                            f"⚠️ <b>Reason:</b> {escape_html(str(e))}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        finally:
            self._active_tasks.pop(job_id, None)

    async def _process_folder_download(
        self, job: DownloadJob, task: ActiveDownloadTask, msg_id: Optional[int]
    ) -> None:
        """Download an entire shared folder recursively, package into ZIP, and deliver."""
        user_id = job.user_id
        job_id = job.id
        source_url = job.source_url
        dir_path = getattr(job, "dir_path", "/") or "/"
        folder_name = job.filename.replace(".zip", "") or "Folder"

        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"🔍 <b>Scanning folder structure...</b>\n📁 <code>{escape_html(folder_name)}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        files = await self.terabox_client.get_all_files_recursive(source_url, root_dir=dir_path)
        if not files:
            raise ValueError(f"No files found inside folder '{folder_name}'.")

        total_files = len(files)
        total_size = sum(it.size for it, _ in files)

        async with get_session() as session:
            await JobRepository.update_status(session, job_id, status="downloading")

        temp_folder_dir = settings.TEMP_DIR / f"folder_{job_id}"
        temp_folder_dir.mkdir(parents=True, exist_ok=True)
        downloaded_bytes_so_far = 0

        for idx, (item, rel_path) in enumerate(files, 1):
            if self.download_mgr.get_cancel_event(job_id).is_set():
                raise DownloadCancelledException(f"Job {job_id} cancelled during folder download.")

            file_dest = temp_folder_dir / rel_path
            file_dest.parent.mkdir(parents=True, exist_ok=True)

            if msg_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=(
                            f"⬇️ <b>Downloading folder files ({idx}/{total_files})...</b>\n\n"
                            f"📄 <b>File:</b> <code>{escape_html(item.filename)}</code>\n"
                            f"📦 <b>Total Size:</b> {format_size(total_size)}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            # Download file: If video without dlink, try M3U8 streaming
            if item.category == FileCategory.VIDEO and not item.dlink:
                segments = await self.terabox_client.get_video_stream_segments(source_url, item)
                if segments:
                    await self.download_mgr.download_m3u8_stream(
                        job_id=f"{job_id}_{idx}",
                        segment_urls=segments,
                        filename=file_dest.name,
                        destination_dir=file_dest.parent,
                        expected_size=item.size,
                    )
                else:
                    target_url = item.thumbnail or source_url
                    await self.download_mgr.download_file(
                        job_id=f"{job_id}_{idx}",
                        url=target_url,
                        filename=file_dest.name,
                        destination_dir=file_dest.parent,
                        expected_size=item.size,
                    )
            elif item.category == FileCategory.IMAGE and not item.dlink and item.thumbnail:
                await self.download_mgr.download_file(
                    job_id=f"{job_id}_{idx}",
                    url=item.thumbnail,
                    filename=file_dest.name,
                    destination_dir=file_dest.parent,
                    expected_size=item.size,
                )
            else:
                target_url = item.dlink or (item.thumbnail if item.thumbnail else source_url)
                await self.download_mgr.download_file(
                    job_id=f"{job_id}_{idx}",
                    url=target_url,
                    filename=file_dest.name,
                    destination_dir=file_dest.parent,
                    expected_size=item.size,
                )

            downloaded_bytes_so_far += item.size
            async with get_session() as session:
                await JobRepository.update_progress(
                    session=session,
                    job_id=job_id,
                    downloaded_bytes=downloaded_bytes_so_far,
                    total_bytes=total_size,
                    speed_bps=0.0,
                    eta_seconds=0,
                )

        # Step 3: Package into ZIP
        async with get_session() as session:
            await JobRepository.update_status(session, job_id, status="packaging")

        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"📦 <b>Compressing {total_files} files into ZIP archive...</b>\n📄 <code>{escape_html(folder_name)}.zip</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        zip_archive_path = ArchiveManager.create_zip_from_directory(
            source_dir=temp_folder_dir,
            archive_name=f"{folder_name}.zip",
        )

        # Cleanup unpacked folder
        try:
            shutil.rmtree(temp_folder_dir, ignore_errors=True)
        except Exception:
            pass

        # Step 4: Deliver ZIP archive
        async with get_session() as session:
            await JobRepository.update_status(session, job_id, status="uploading")

        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"📤 <b>Delivering ZIP archive...</b>\n📄 <code>{escape_html(zip_archive_path.name)}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        force_link = job.delivery_method == "direct_link"
        delivered = await self.sender.deliver_file(
            chat_id=user_id,
            file_path=zip_archive_path,
            filename=zip_archive_path.name,
            category=FileCategory.ARCHIVE,
            job_id=job_id,
            force_link=force_link,
        )

        if delivered:
            file_bytes = zip_archive_path.stat().st_size if zip_archive_path.exists() else 0
            async with get_session() as session:
                await JobRepository.update_status(
                    session,
                    job_id,
                    status="completed",
                    local_path=str(zip_archive_path),
                )
                await UserRepository.add_download_bytes(session, user_id, file_bytes)
            if msg_id:
                try:
                    await self.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception:
                    pass
        else:
            raise IOError("Failed to deliver folder ZIP archive.")

    async def _process_file_download(
        self, job: DownloadJob, task: ActiveDownloadTask, msg_id: Optional[int], dir_path: str
    ) -> None:
        """Download an individual file and deliver it."""
        user_id = job.user_id
        job_id = job.id
        source_url = job.source_url
        filename = job.filename
        fs_id = job.fs_id

        # Resolve metadata in specified folder or root
        share_res = await self.terabox_client.get_share_info(source_url, dir_path=dir_path)
        if not share_res.success:
            share_res = await self.terabox_client.get_share_info(source_url, dir_path="/")

        target_item: Optional[TeraBoxItem] = None
        if fs_id:
            for it in share_res.items:
                if it.fs_id == fs_id:
                    target_item = it
                    break

        if not target_item and share_res.items:
            # If item not in current directory list, search recursively
            all_rec = await self.terabox_client.get_all_files_recursive(source_url, root_dir="/")
            for it, _ in all_rec:
                if it.fs_id == fs_id:
                    target_item = it
                    break
            if not target_item:
                target_item = share_res.items[0]

        if not target_item:
            raise ValueError(f"File '{filename}' not found on TeraBox.")

        async with get_session() as session:
            await JobRepository.update_status(session, job_id, status="downloading")

        async def on_progress(p: DownloadProgress):
            async with get_session() as session:
                await JobRepository.update_progress(
                    session=session,
                    job_id=job_id,
                    downloaded_bytes=p.downloaded_bytes,
                    total_bytes=p.total_bytes,
                    speed_bps=p.speed_bps,
                    eta_seconds=p.eta_seconds,
                )

            now = time.time()
            if msg_id and (now - task.last_ui_update >= 3.5):
                bar = generate_progress_bar(p.percentage)
                cur_size = format_size(p.downloaded_bytes)
                tot_size = format_size(p.total_bytes) if p.total_bytes > 0 else "Unknown"
                speed = format_speed(p.speed_bps)
                eta = format_seconds(p.eta_seconds)
                text = (
                    f"⬇️ <b>Downloading...</b>\n\n"
                    f"📄 <b>File:</b> <code>{escape_html(filename)}</code>\n"
                    f"{bar}\n"
                    f"📦 <b>Size:</b> {cur_size} / {tot_size}\n"
                    f"⚡️ <b>Speed:</b> {speed}\n"
                    f"⏳ <b>ETA:</b> {eta}"
                )
                try:
                    await self.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                task.last_ui_update = now

        # Download logic
        downloaded_path = None
        if target_item.category == FileCategory.VIDEO and not target_item.dlink:
            segments = await self.terabox_client.get_video_stream_segments(source_url, target_item)
            if segments:
                downloaded_path = await self.download_mgr.download_m3u8_stream(
                    job_id=job_id,
                    segment_urls=segments,
                    filename=target_item.filename,
                    expected_size=target_item.size,
                    progress_callback=on_progress,
                )

        if not downloaded_path:
            target_url = target_item.dlink or (target_item.thumbnail if target_item.category == FileCategory.IMAGE else source_url)
            downloaded_path = await self.download_mgr.download_file(
                job_id=job_id,
                url=target_url,
                filename=target_item.filename,
                expected_size=target_item.size,
                progress_callback=on_progress,
            )

        # Upload & Deliver
        async with get_session() as session:
            await JobRepository.update_status(session, job_id, status="uploading")

        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"📤 <b>Delivering file...</b>\n📄 <code>{escape_html(downloaded_path.name)}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        force_link = job.delivery_method == "direct_link"
        delivered = await self.sender.deliver_file(
            chat_id=user_id,
            file_path=downloaded_path,
            filename=downloaded_path.name,
            category=target_item.category,
            job_id=job_id,
            force_link=force_link,
        )

        if delivered:
            file_bytes = downloaded_path.stat().st_size if downloaded_path.exists() else 0
            async with get_session() as session:
                await JobRepository.update_status(
                    session,
                    job_id,
                    status="completed",
                    local_path=str(downloaded_path),
                )
                await UserRepository.add_download_bytes(session, user_id, file_bytes)

            if msg_id:
                try:
                    await self.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception:
                    pass
        else:
            raise IOError("Failed to deliver file via Telegram or temporary link.")
