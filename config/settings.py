from pathlib import Path
from typing import List, Union
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Bot Authentication
    BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    ADMIN_IDS: List[int] = []

    # Database & Cache
    DATABASE_URL: str = "sqlite+aiosqlite:///./bot.db"
    REDIS_URL: Union[str, None] = None

    # Storage Paths
    DOWNLOAD_DIR: Path = Path("./storage/downloads")
    TEMP_DIR: Path = Path("./storage/temp")
    ARCHIVE_DIR: Path = Path("./storage/archives")

    # Limits & Concurrency
    MAX_FILE_SIZE_MB: int = 2000
    MAX_TG_UPLOAD_SIZE_MB: int = 50  # 50MB for cloud Telegram Bot API; 2000MB if using local server
    LOCAL_BOT_API_SERVER_URL: Union[str, None] = None

    MAX_CONCURRENT_DOWNLOADS: int = 10
    MAX_USER_ACTIVE_JOBS: int = 0  # 0 or negative = unlimited downloads per user
    MAX_QUEUE_SIZE: int = 1000
    DOWNLOAD_RETRIES: int = 3
    CHUNK_SIZE: int = 64 * 1024  # 64 KB streaming chunks

    # Private Temporary Link Server
    TEMP_SERVER_HOST: str = "0.0.0.0"
    TEMP_SERVER_PORT: int = Field(default=8080, validation_alias=AliasChoices("PORT", "TEMP_SERVER_PORT"))
    TEMP_SERVER_BASE_URL: str = "http://localhost:8080"
    TEMP_LINK_EXPIRY_HOURS: int = 6
    AUTO_DELETE_TEMP_FILES: bool = True
    CLEANUP_INTERVAL_MINUTES: int = 15
    MIN_FREE_DISK_SPACE_MB: int = 500

    # TeraBox Credentials / Options
    TERABOX_COOKIE: Union[str, None] = None
    TERABOX_API_URL: Union[str, None] = None

    # Logging
    LOG_LEVEL: str = "INFO"

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Union[str, int, List[Union[int, str]]]) -> List[int]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []

    def ensure_directories(self) -> None:
        """Create necessary directories if they do not exist."""
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        Path("./logs").mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
