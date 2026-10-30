from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import DownloadJob, TempDownloadLink, User, utc_now


class UserRepository:
    @staticmethod
    async def get_or_create(
        session: AsyncSession,
        telegram_id: int,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
    ) -> User:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
            )
            session.add(user)
            await session.flush()
        else:
            updated = False
            if username and user.username != username:
                user.username = username
                updated = True
            if full_name and user.full_name != full_name:
                user.full_name = full_name
                updated = True
            if updated:
                user.updated_at = utc_now()
                await session.flush()
        return user

    @staticmethod
    async def get_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_blocked(session: AsyncSession, telegram_id: int, blocked: bool) -> bool:
        stmt = update(User).where(User.telegram_id == telegram_id).values(is_blocked=blocked, updated_at=utc_now())
        res = await session.execute(stmt)
        return res.rowcount > 0

    @staticmethod
    async def set_preferred_delivery(session: AsyncSession, telegram_id: int, method: str) -> None:
        stmt = update(User).where(User.telegram_id == telegram_id).values(preferred_delivery=method, updated_at=utc_now())
        await session.execute(stmt)

    @staticmethod
    async def add_download_bytes(session: AsyncSession, telegram_id: int, count: int) -> None:
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(
                total_downloads_bytes=User.total_downloads_bytes + count,
                updated_at=utc_now(),
            )
        )
        await session.execute(stmt)

    @staticmethod
    async def count_users(session: AsyncSession) -> int:
        stmt = select(func.count(User.telegram_id))
        res = await session.execute(stmt)
        return res.scalar_one() or 0

    @staticmethod
    async def get_all_users(session: AsyncSession, limit: int = 1000) -> List[User]:
        stmt = select(User).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())


class JobRepository:
    @staticmethod
    async def create_job(
        session: AsyncSession,
        user_id: int,
        source_url: str,
        filename: str,
        file_size: int = 0,
        fs_id: Optional[str] = None,
        category: str = "other",
        delivery_method: str = "telegram",
        dir_path: str = "/",
    ) -> DownloadJob:
        job = DownloadJob(
            user_id=user_id,
            source_url=source_url,
            dir_path=dir_path,
            filename=filename,
            file_size=file_size,
            fs_id=fs_id,
            category=category,
            status="queued",
            delivery_method=delivery_method,
        )
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def get_job(session: AsyncSession, job_id: str) -> Optional[DownloadJob]:
        stmt = select(DownloadJob).where(DownloadJob.id == job_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        job_id: str,
        status: str,
        error_message: Optional[str] = None,
        local_path: Optional[str] = None,
        temp_link_token: Optional[str] = None,
    ) -> None:
        values: dict = {
            "status": status,
            "updated_at": utc_now(),
        }
        if error_message is not None:
            values["error_message"] = error_message
        if local_path is not None:
            values["local_path"] = local_path
        if temp_link_token is not None:
            values["temp_link_token"] = temp_link_token
        if status in ("completed", "failed", "cancelled"):
            values["completed_at"] = utc_now()

        stmt = update(DownloadJob).where(DownloadJob.id == job_id).values(**values)
        await session.execute(stmt)

    @staticmethod
    async def update_progress(
        session: AsyncSession,
        job_id: str,
        downloaded_bytes: int,
        total_bytes: int,
        speed_bps: float,
        eta_seconds: int,
    ) -> None:
        pct = (downloaded_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
        values = {
            "downloaded_bytes": downloaded_bytes,
            "progress_pct": min(100.0, pct),
            "speed_bps": speed_bps,
            "eta_seconds": eta_seconds,
            "updated_at": utc_now(),
        }
        if total_bytes > 0:
            values["file_size"] = total_bytes

        stmt = update(DownloadJob).where(DownloadJob.id == job_id).values(**values)
        await session.execute(stmt)

    @staticmethod
    async def get_user_jobs(
        session: AsyncSession,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
    ) -> List[DownloadJob]:
        stmt = (
            select(DownloadJob)
            .where(DownloadJob.user_id == user_id)
            .order_by(desc(DownloadJob.created_at))
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def count_user_active_jobs(session: AsyncSession, user_id: int) -> int:
        active_statuses = ["queued", "analyzing", "downloading", "packaging", "uploading"]
        stmt = select(func.count(DownloadJob.id)).where(
            DownloadJob.user_id == user_id,
            DownloadJob.status.in_(active_statuses),
        )
        res = await session.execute(stmt)
        return res.scalar_one() or 0

    @staticmethod
    async def get_queued_jobs(session: AsyncSession, limit: int = 10) -> List[DownloadJob]:
        stmt = (
            select(DownloadJob)
            .where(DownloadJob.status == "queued")
            .order_by(DownloadJob.created_at)
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_active_jobs(session: AsyncSession) -> List[DownloadJob]:
        active_statuses = ["analyzing", "downloading", "packaging", "uploading"]
        stmt = (
            select(DownloadJob)
            .where(DownloadJob.status.in_(active_statuses))
            .order_by(DownloadJob.created_at)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def count_by_status(session: AsyncSession) -> Dict[str, int]:
        stmt = select(DownloadJob.status, func.count(DownloadJob.id)).group_by(DownloadJob.status)
        res = await session.execute(stmt)
        return {row[0]: row[1] for row in res.all()}

    @staticmethod
    async def cancel_user_jobs(session: AsyncSession, user_id: int) -> int:
        active_statuses = ["queued", "analyzing", "downloading", "packaging", "uploading"]
        stmt = (
            update(DownloadJob)
            .where(
                DownloadJob.user_id == user_id,
                DownloadJob.status.in_(active_statuses),
            )
            .values(status="cancelled", completed_at=utc_now(), updated_at=utc_now())
        )
        res = await session.execute(stmt)
        return res.rowcount

    @staticmethod
    async def get_recent_failed_jobs(session: AsyncSession, limit: int = 10) -> List[DownloadJob]:
        stmt = (
            select(DownloadJob)
            .where(DownloadJob.status == "failed")
            .order_by(desc(DownloadJob.updated_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_total_stats(session: AsyncSession) -> Dict[str, int]:
        stmt = select(
            func.count(DownloadJob.id),
            func.sum(DownloadJob.downloaded_bytes),
        )
        res = await session.execute(stmt)
        row = res.one_or_none()
        total_jobs = row[0] if row and row[0] is not None else 0
        total_bytes = row[1] if row and row[1] is not None else 0
        return {"total_jobs": total_jobs, "total_bytes": total_bytes}


class TempLinkRepository:
    @staticmethod
    async def create_link(
        session: AsyncSession,
        token: str,
        user_id: int,
        file_path: str,
        filename: str,
        file_size: int,
        expires_at: datetime,
        job_id: Optional[str] = None,
    ) -> TempDownloadLink:
        link = TempDownloadLink(
            token=token,
            job_id=job_id,
            user_id=user_id,
            file_path=file_path,
            filename=filename,
            file_size=file_size,
            expires_at=expires_at,
        )
        session.add(link)
        await session.flush()
        return link

    @staticmethod
    async def get_link(session: AsyncSession, token: str) -> Optional[TempDownloadLink]:
        stmt = select(TempDownloadLink).where(TempDownloadLink.token == token)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def increment_download_count(session: AsyncSession, token: str) -> None:
        stmt = (
            update(TempDownloadLink)
            .where(TempDownloadLink.token == token)
            .values(download_count=TempDownloadLink.download_count + 1)
        )
        await session.execute(stmt)

    @staticmethod
    async def get_expired_links(session: AsyncSession) -> List[TempDownloadLink]:
        now = utc_now()
        stmt = select(TempDownloadLink).where(TempDownloadLink.expires_at <= now)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def delete_link(session: AsyncSession, token: str) -> None:
        stmt = select(TempDownloadLink).where(TempDownloadLink.token == token)
        res = await session.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            await session.delete(item)
