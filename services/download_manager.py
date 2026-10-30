import asyncio
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Coroutine, Dict, List, Optional
import aiofiles
import aiohttp
from config.settings import settings
from utils.helpers import sanitize_filename
from utils.logger import logger
from utils.validators import check_ssrf


@dataclass
class DownloadProgress:
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: int = 0
    percentage: float = 0.0


ProgressCallback = Callable[[DownloadProgress], Coroutine[None, None, None]]


class DownloadCancelledException(Exception):
    pass


class DownloadManager:
    """
    Robust asynchronous streaming download manager with:
    - Resume interrupted downloads (HTTP Range)
    - Concurrency semaphore
    - Moving average speed & ETA calculation
    - Cancellation token support
    - Automatic exponential backoff retries
    - Disk space check
    - Chunked streaming (no high memory usage)
    """

    def __init__(self, max_concurrent: Optional[int] = None):
        self.semaphore = asyncio.Semaphore(max_concurrent or settings.MAX_CONCURRENT_DOWNLOADS)
        self._cancellation_events: Dict[str, asyncio.Event] = {}
        self._cancelled_jobs: set[str] = set()

    def get_cancel_event(self, job_id: str) -> asyncio.Event:
        if job_id not in self._cancellation_events:
            ev = asyncio.Event()
            if job_id in self._cancelled_jobs:
                ev.set()
            self._cancellation_events[job_id] = ev
        return self._cancellation_events[job_id]

    def cancel_job(self, job_id: str) -> bool:
        self._cancelled_jobs.add(job_id)
        event = self._cancellation_events.get(job_id)
        if event:
            event.set()
        return True

    def remove_cancel_event(self, job_id: str) -> None:
        self._cancellation_events.pop(job_id, None)
        self._cancelled_jobs.discard(job_id)

    async def download_file(
        self,
        job_id: str,
        url: str,
        filename: str,
        destination_dir: Optional[Path] = None,
        expected_size: int = 0,
        headers: Optional[Dict[str, str]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        max_retries: Optional[int] = None,
    ) -> Path:
        """
        Download a file streamingly with resume, cancellation, and progress updates.
        Returns the Path to the completed file.
        """
        async with self.semaphore:
            cancel_event = self.get_cancel_event(job_id)
            if cancel_event.is_set() or job_id in self._cancelled_jobs:
                self.remove_cancel_event(job_id)
                raise DownloadCancelledException(f"Job {job_id} was cancelled before download.")
            cancel_event.clear()

            dest_dir = destination_dir or settings.DOWNLOAD_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)

            safe_name = sanitize_filename(filename)
            final_file_path = dest_dir / safe_name
            part_file_path = dest_dir / f"{safe_name}.part"

            retries = max_retries if max_retries is not None else settings.DOWNLOAD_RETRIES
            delay = 2

            for attempt in range(1, retries + 1):
                if cancel_event.is_set():
                    raise DownloadCancelledException(f"Job {job_id} was cancelled before download.")

                try:
                    await self._download_attempt(
                        job_id=job_id,
                        url=url,
                        final_path=final_file_path,
                        part_path=part_file_path,
                        expected_size=expected_size,
                        custom_headers=headers,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                    self.remove_cancel_event(job_id)
                    return final_file_path

                except DownloadCancelledException:
                    logger.info(f"Download of job {job_id} cancelled.")
                    if part_file_path.exists():
                        try:
                            part_file_path.unlink()
                        except Exception:
                            pass
                    self.remove_cancel_event(job_id)
                    raise

                except Exception as e:
                    logger.warning(
                        f"Download attempt {attempt}/{retries} for job {job_id} failed: {e}"
                    )
                    if attempt >= retries:
                        self.remove_cancel_event(job_id)
                        raise
                    await asyncio.sleep(delay)
                    delay *= 2

            self.remove_cancel_event(job_id)
            return final_file_path

    async def download_m3u8_stream(
        self,
        job_id: str,
        segment_urls: List[str],
        filename: str,
        destination_dir: Optional[Path] = None,
        expected_size: int = 0,
        headers: Optional[Dict[str, str]] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Path:
        """
        Download an M3U8 video stream segment-by-segment and assemble into an MP4 file.
        Tracks progress per segment, respects cancellation, and remuxes with ffmpeg.
        """
        async with self.semaphore:
            cancel_event = self.get_cancel_event(job_id)
            if cancel_event.is_set() or job_id in self._cancelled_jobs:
                self.remove_cancel_event(job_id)
                raise DownloadCancelledException(f"Job {job_id} was cancelled before download.")
            cancel_event.clear()

            dest_dir = destination_dir or settings.DOWNLOAD_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)

            safe_name = sanitize_filename(filename)
            if not safe_name.lower().endswith((".mp4", ".mkv", ".ts")):
                safe_name += ".mp4"

            final_file_path = dest_dir / safe_name
            temp_work_dir = settings.TEMP_DIR / f"m3u8_{job_id}"
            temp_work_dir.mkdir(parents=True, exist_ok=True)
            combined_ts_path = temp_work_dir / "combined.ts"

            default_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.terabox.app/",
            }
            if headers:
                default_headers.update(headers)

            total_segments = len(segment_urls)
            downloaded_bytes = 0
            start_time = time.time()
            last_cb_time = start_time

            logger.info(f"Starting M3U8 stream download for '{safe_name}' ({total_segments} segments)")

            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=45)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    with open(combined_ts_path, "wb") as outfile:
                        for idx, seg_url in enumerate(segment_urls):
                            if cancel_event.is_set():
                                raise DownloadCancelledException(f"Job {job_id} cancelled during stream download.")

                            # SSRF safety
                            if not check_ssrf(seg_url):
                                logger.warning(f"Skipping unsafe segment URL: {seg_url}")
                                continue

                            # Fetch segment
                            async with session.get(seg_url, headers=default_headers) as resp:
                                if resp.status == 200:
                                    chunk = await resp.read()
                                    outfile.write(chunk)
                                    downloaded_bytes += len(chunk)
                                else:
                                    logger.warning(f"Segment {idx+1}/{total_segments} returned HTTP {resp.status}")

                            now = time.time()
                            elapsed = max(0.001, now - start_time)
                            speed = downloaded_bytes / elapsed
                            pct = min(99.0, ((idx + 1) / total_segments) * 100.0)
                            est_total = int((downloaded_bytes / (idx + 1)) * total_segments) if (idx + 1) > 0 else expected_size
                            remaining_bytes = max(0, est_total - downloaded_bytes)
                            eta = int(remaining_bytes / speed) if speed > 0 else 0

                            if progress_callback and (now - last_cb_time >= 2.0 or idx == total_segments - 1):
                                last_cb_time = now
                                prog = DownloadProgress(
                                    downloaded_bytes=downloaded_bytes,
                                    total_bytes=est_total,
                                    speed_bps=speed,
                                    eta_seconds=eta,
                                    percentage=pct,
                                )
                                try:
                                    await progress_callback(prog)
                                except Exception:
                                    pass

                # Check if combined TS has data
                if not combined_ts_path.exists() or combined_ts_path.stat().st_size == 0:
                    raise IOError(f"Failed to download any video stream segments for '{filename}'")

                # Remux to MP4 using ffmpeg if available
                ffmpeg_bin = shutil.which("ffmpeg")
                remux_success = False
                if ffmpeg_bin:
                    cmd = [ffmpeg_bin, "-y", "-i", str(combined_ts_path), "-c", "copy", str(final_file_path)]
                    logger.info(f"Remuxing TS stream to MP4 using ffmpeg: {' '.join(cmd)}")
                    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if proc.returncode == 0 and final_file_path.exists() and final_file_path.stat().st_size > 0:
                        remux_success = True

                if not remux_success:
                    # Fallback: rename combined.ts directly to destination
                    shutil.move(str(combined_ts_path), str(final_file_path))

                logger.info(
                    f"M3U8 download completed: '{final_file_path.name}' ({final_file_path.stat().st_size} bytes)"
                )
                return final_file_path

            finally:
                self.remove_cancel_event(job_id)
                # Cleanup temp work directory
                if temp_work_dir.exists():
                    try:
                        shutil.rmtree(temp_work_dir, ignore_errors=True)
                    except Exception:
                        pass

    async def _download_attempt(
        self,
        job_id: str,
        url: str,
        final_path: Path,
        part_path: Path,
        expected_size: int,
        custom_headers: Optional[Dict[str, str]],
        progress_callback: Optional[ProgressCallback],
        cancel_event: asyncio.Event,
    ) -> None:
        # SSRF validation
        if not check_ssrf(url):
            raise ValueError(f"SSRF violation: destination URL {url} cannot be downloaded.")

        req_headers = dict(custom_headers or {})
        start_byte = 0

        # Check existing partial download for HTTP Range resume
        if part_path.exists():
            start_byte = part_path.stat().st_size
            if start_byte > 0:
                req_headers["Range"] = f"bytes={start_byte}-"
                logger.info(f"Resuming download of '{part_path.name}' from byte {start_byte}")

        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=req_headers) as response:
                if response.status not in (200, 206):
                    raise IOError(f"Server returned HTTP {response.status}: {response.reason}")

                # Determine total size
                content_range = response.headers.get("Content-Range")
                if content_range and "/" in content_range:
                    try:
                        total_size = int(content_range.split("/")[-1])
                    except ValueError:
                        total_size = expected_size
                elif response.status == 200:
                    start_byte = 0
                    total_size = int(response.headers.get("Content-Length", expected_size))
                else:
                    total_size = expected_size

                # Pre-flight disk space check
                remaining_bytes = max(0, total_size - start_byte)
                total, used, free = shutil.disk_usage(part_path.parent)
                margin = settings.MIN_FREE_DISK_SPACE_MB * 1024 * 1024
                if free < (remaining_bytes + margin):
                    raise IOError(
                        f"Insufficient disk space. Needed: {remaining_bytes} bytes, Available: {free} bytes"
                    )

                # Open mode: append if resuming (206 Partial Content), write if fresh (200 OK)
                file_mode = "ab" if (response.status == 206 and start_byte > 0) else "wb"
                downloaded = start_byte

                # Progress & speed variables
                last_time = time.time()
                last_downloaded = downloaded
                speed_bps = 0.0
                last_callback_time = 0.0

                async with aiofiles.open(part_path, mode=file_mode) as f:
                    async for chunk in response.content.iter_chunked(settings.CHUNK_SIZE):
                        if cancel_event.is_set():
                            raise DownloadCancelledException(f"Job {job_id} was cancelled.")

                        await f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        time_delta = now - last_time
                        if time_delta >= 1.0:
                            bytes_delta = downloaded - last_downloaded
                            current_speed = bytes_delta / time_delta
                            # Smooth moving average speed
                            speed_bps = 0.7 * speed_bps + 0.3 * current_speed if speed_bps > 0 else current_speed
                            last_time = now
                            last_downloaded = downloaded

                        # Notify progress callback at most once every 1.5 seconds to avoid spamming
                        if progress_callback and (now - last_callback_time >= 1.5):
                            eta = int((total_size - downloaded) / speed_bps) if (speed_bps > 0 and total_size > downloaded) else 0
                            pct = (downloaded / total_size * 100.0) if total_size > 0 else 0.0
                            prog = DownloadProgress(
                                downloaded_bytes=downloaded,
                                total_bytes=total_size,
                                speed_bps=speed_bps,
                                eta_seconds=eta,
                                percentage=pct,
                            )
                            try:
                                await progress_callback(prog)
                            except Exception as cb_err:
                                logger.debug(f"Progress callback error: {cb_err}")
                            last_callback_time = now

        # Rename .part file to final destination atomically
        if final_path.exists():
            final_path.unlink()
        part_path.rename(final_path)

        # Final 100% callback
        if progress_callback:
            final_prog = DownloadProgress(
                downloaded_bytes=downloaded,
                total_bytes=downloaded,
                speed_bps=0.0,
                eta_seconds=0,
                percentage=100.0,
            )
            try:
                await progress_callback(final_prog)
            except Exception:
                pass

        logger.info(f"Download complete: '{final_path}' ({downloaded} bytes)")
