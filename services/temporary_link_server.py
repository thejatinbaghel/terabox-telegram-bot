from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn
from config.settings import settings
from database.database import get_session
from database.repositories import TempLinkRepository
from utils.logger import logger
from utils.validators import is_safe_path

app = FastAPI(title="TeraBox Temporary Download Server", docs_url=None, redoc_url=None)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/d/{token}")
async def download_file_by_token(token: str, request: Request):
    """
    Secure file delivery endpoint with:
    - Unguessable token validation
    - Expiration check
    - Anti-directory-traversal protection
    - HTTP Range requests support (chunked streaming & resume)
    - Download counter
    """
    async with get_session() as session:
        link_record = await TempLinkRepository.get_link(session, token)
        if not link_record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download link not found.")

        if link_record.is_expired():
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="This download link has expired.")

        file_path = Path(link_record.file_path)

        # Path traversal check
        is_safe = is_safe_path(settings.DOWNLOAD_DIR, file_path) or is_safe_path(
            settings.ARCHIVE_DIR, file_path
        ) or is_safe_path(settings.TEMP_DIR, file_path)

        if not is_safe or not file_path.exists() or not file_path.is_file():
            logger.error(f"Security/File error: path '{file_path}' unsafe or missing for token {token}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is no longer available.")

        # Increment download count
        await TempLinkRepository.increment_download_count(session, token)

    file_size = file_path.stat().st_size
    filename = link_record.filename

    # Support HTTP Range requests
    range_header = request.headers.get("Range")
    start = 0
    end = file_size - 1

    status_code = status.HTTP_200_OK
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "application/octet-stream",
    }

    if range_header:
        # Format: bytes=start-end
        range_parts = range_header.replace("bytes=", "").split("-")
        try:
            if range_parts[0]:
                start = int(range_parts[0])
            if len(range_parts) > 1 and range_parts[1]:
                end = int(range_parts[1])
            if start > end or start >= file_size:
                return Response(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
            status_code = status.HTTP_206_PARTIAL_CONTENT
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        except ValueError:
            return Response(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)

    content_length = end - start + 1
    headers["Content-Length"] = str(content_length)

    async def file_chunk_generator(path: Path, offset: int, length: int, chunk_size: int = 64 * 1024):
        bytes_left = length
        with open(path, "rb") as f:
            f.seek(offset)
            while bytes_left > 0:
                read_size = min(chunk_size, bytes_left)
                data = f.read(read_size)
                if not data:
                    break
                bytes_left -= len(data)
                yield data

    return StreamingResponse(
        file_chunk_generator(file_path, start, content_length),
        status_code=status_code,
        headers=headers,
    )


class TemporaryLinkServer:
    """Manages creation of secure temporary links and running the async web server."""

    @staticmethod
    async def create_download_link(
        user_id: int,
        file_path: Path,
        filename: str,
        expiry_hours: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> tuple[str, datetime]:
        """
        Register a new temporary download link in the database.
        Returns (full_download_url, expires_at_datetime).
        """
        token = secrets.token_urlsafe(32)
        hours = expiry_hours or settings.TEMP_LINK_EXPIRY_HOURS
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        file_size = file_path.stat().st_size if file_path.exists() else 0

        async with get_session() as session:
            await TempLinkRepository.create_link(
                session=session,
                token=token,
                job_id=job_id,
                user_id=user_id,
                file_path=str(file_path.resolve()),
                filename=filename,
                file_size=file_size,
                expires_at=expires_at,
            )

        base_url = settings.TEMP_SERVER_BASE_URL.rstrip("/")
        download_url = f"{base_url}/d/{token}"
        logger.info(f"Created temporary download link for user {user_id}: {token} (expires: {expires_at})")
        return download_url, expires_at

    @staticmethod
    async def start_server() -> None:
        """Start the FastAPI uvicorn server asynchronously."""
        config = uvicorn.Config(
            app=app,
            host=settings.TEMP_SERVER_HOST,
            port=settings.TEMP_SERVER_PORT,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        logger.info(
            f"Temporary link server running at http://{settings.TEMP_SERVER_HOST}:{settings.TEMP_SERVER_PORT}"
        )
        await server.serve()
