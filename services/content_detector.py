from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from utils.helpers import format_size


class FileCategory(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    FOLDER = "folder"
    OTHER = "other"


# Extension mapping
EXT_CATEGORY_MAP: Dict[str, FileCategory] = {
    # Video
    ".mp4": FileCategory.VIDEO,
    ".mkv": FileCategory.VIDEO,
    ".avi": FileCategory.VIDEO,
    ".mov": FileCategory.VIDEO,
    ".flv": FileCategory.VIDEO,
    ".wmv": FileCategory.VIDEO,
    ".webm": FileCategory.VIDEO,
    ".m4v": FileCategory.VIDEO,
    ".3gp": FileCategory.VIDEO,
    ".ts": FileCategory.VIDEO,
    # Image
    ".jpg": FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE,
    ".png": FileCategory.IMAGE,
    ".webp": FileCategory.IMAGE,
    ".gif": FileCategory.IMAGE,
    ".bmp": FileCategory.IMAGE,
    ".tiff": FileCategory.IMAGE,
    ".svg": FileCategory.IMAGE,
    ".ico": FileCategory.IMAGE,
    # Audio
    ".mp3": FileCategory.AUDIO,
    ".wav": FileCategory.AUDIO,
    ".aac": FileCategory.AUDIO,
    ".flac": FileCategory.AUDIO,
    ".ogg": FileCategory.AUDIO,
    ".m4a": FileCategory.AUDIO,
    ".wma": FileCategory.AUDIO,
    ".opus": FileCategory.AUDIO,
    ".m4r": FileCategory.AUDIO,
    # Document
    ".pdf": FileCategory.DOCUMENT,
    ".docx": FileCategory.DOCUMENT,
    ".doc": FileCategory.DOCUMENT,
    ".txt": FileCategory.DOCUMENT,
    ".xlsx": FileCategory.DOCUMENT,
    ".xls": FileCategory.DOCUMENT,
    ".pptx": FileCategory.DOCUMENT,
    ".ppt": FileCategory.DOCUMENT,
    ".csv": FileCategory.DOCUMENT,
    ".rtf": FileCategory.DOCUMENT,
    ".epub": FileCategory.DOCUMENT,
    # Archive
    ".zip": FileCategory.ARCHIVE,
    ".rar": FileCategory.ARCHIVE,
    ".7z": FileCategory.ARCHIVE,
    ".tar": FileCategory.ARCHIVE,
    ".gz": FileCategory.ARCHIVE,
    ".bz2": FileCategory.ARCHIVE,
    ".xz": FileCategory.ARCHIVE,
    ".iso": FileCategory.ARCHIVE,
}

CATEGORY_ICONS = {
    FileCategory.VIDEO: "🎬",
    FileCategory.IMAGE: "🖼",
    FileCategory.AUDIO: "🎵",
    FileCategory.DOCUMENT: "📄",
    FileCategory.ARCHIVE: "📦",
    FileCategory.FOLDER: "📁",
    FileCategory.OTHER: "📎",
}


@dataclass
class TeraBoxItem:
    fs_id: str
    filename: str
    size: int
    is_dir: bool
    path: str
    category: FileCategory
    dlink: Optional[str] = None
    thumbnail: Optional[str] = None
    sign: Optional[str] = None
    timestamp: Optional[str] = None
    shareid: Optional[str] = None
    uk: Optional[str] = None
    js_token: Optional[str] = None
    children: List["TeraBoxItem"] = field(default_factory=list)

    @property
    def formatted_size(self) -> str:
        return format_size(self.size)

    @property
    def icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "📎")


class ContentScenario(str, Enum):
    SINGLE_VIDEO = "single_video"
    MULTIPLE_VIDEOS = "multiple_videos"
    MULTIPLE_IMAGES = "multiple_images"
    FOLDERS = "folders"
    MIXED = "mixed"


@dataclass
class ContentAnalysis:
    scenario: ContentScenario
    total_files: int
    total_folders: int
    total_size: int
    items: List[TeraBoxItem]
    category_counts: Dict[FileCategory, int]
    category_sizes: Dict[FileCategory, int]

    @property
    def formatted_total_size(self) -> str:
        return format_size(self.total_size)


class ContentDetector:
    """Classifies files, organizes categories, and determines download/browser scenario."""

    @staticmethod
    def detect_category(filename: str, is_dir: bool = False) -> FileCategory:
        if is_dir:
            return FileCategory.FOLDER
        ext = Path(filename).suffix.lower()
        return EXT_CATEGORY_MAP.get(ext, FileCategory.OTHER)

    @classmethod
    def analyze(cls, items: List[TeraBoxItem]) -> ContentAnalysis:
        category_counts: Dict[FileCategory, int] = {cat: 0 for cat in FileCategory}
        category_sizes: Dict[FileCategory, int] = {cat: 0 for cat in FileCategory}

        total_files = 0
        total_folders = 0
        total_size = 0

        for item in items:
            if item.is_dir:
                total_folders += 1
                category_counts[FileCategory.FOLDER] += 1
            else:
                total_files += 1
                total_size += item.size
                category_counts[item.category] += 1
                category_sizes[item.category] += item.size

        # Determine Scenario
        if total_folders > 0:
            scenario = ContentScenario.FOLDERS
        elif total_files == 1 and category_counts[FileCategory.VIDEO] == 1:
            scenario = ContentScenario.SINGLE_VIDEO
        elif total_files > 1 and category_counts[FileCategory.VIDEO] == total_files:
            scenario = ContentScenario.MULTIPLE_VIDEOS
        elif total_files > 1 and category_counts[FileCategory.IMAGE] == total_files:
            scenario = ContentScenario.MULTIPLE_IMAGES
        else:
            scenario = ContentScenario.MIXED

        return ContentAnalysis(
            scenario=scenario,
            total_files=total_files,
            total_folders=total_folders,
            total_size=total_size,
            items=items,
            category_counts=category_counts,
            category_sizes=category_sizes,
        )
