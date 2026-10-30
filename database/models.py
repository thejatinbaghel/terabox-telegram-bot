from datetime import datetime, timezone
import uuid
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    is_blocked = Column(Boolean, default=False, nullable=False)
    preferred_delivery = Column(String(32), default="telegram", nullable=False)  # "telegram" | "direct_link"
    total_downloads_bytes = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    jobs = relationship("DownloadJob", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User {self.telegram_id} username={self.username} blocked={self.is_blocked}>"


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    source_url = Column(Text, nullable=False)
    dir_path = Column(String(512), default="/", nullable=True)
    fs_id = Column(String(64), nullable=True)
    filename = Column(String(255), nullable=False)
    file_size = Column(BigInteger, default=0, nullable=False)
    category = Column(String(32), default="other", nullable=False)
    status = Column(
        String(32),
        default="queued",
        nullable=False,
        index=True
    )  # queued, analyzing, downloading, packaging, uploading, completed, failed, cancelled
    progress_pct = Column(Float, default=0.0, nullable=False)
    downloaded_bytes = Column(BigInteger, default=0, nullable=False)
    speed_bps = Column(Float, default=0.0, nullable=False)
    eta_seconds = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    delivery_method = Column(String(32), default="telegram", nullable=False)
    local_path = Column(String(512), nullable=True)
    temp_link_token = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="jobs")

    def __repr__(self) -> str:
        return f"<DownloadJob {self.id} file={self.filename} status={self.status} user={self.user_id}>"


class TempDownloadLink(Base):
    __tablename__ = "temp_download_links"

    token = Column(String(64), primary_key=True, index=True)
    job_id = Column(String(36), nullable=True, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    file_path = Column(String(512), nullable=False)
    filename = Column(String(255), nullable=False)
    file_size = Column(BigInteger, default=0, nullable=False)
    download_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return utc_now() >= exp

    def __repr__(self) -> str:
        return f"<TempDownloadLink token={self.token} file={self.filename} expires_at={self.expires_at}>"
