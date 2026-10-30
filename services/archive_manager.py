import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from config.settings import settings
from utils.helpers import sanitize_filename
from utils.logger import logger


@dataclass
class ArchiveItem:
    local_path: Path
    arcname: str  # Path relative to root of archive


class ArchiveManager:
    """Handles smart folder ZIP packaging, duplicate filename resolution, and disk space checks."""

    @staticmethod
    def check_free_space(required_bytes: int, target_dir: Path) -> bool:
        """Ensure there is enough free disk space with a safety margin."""
        try:
            total, used, free = shutil.disk_usage(target_dir)
            margin = settings.MIN_FREE_DISK_SPACE_MB * 1024 * 1024
            return free > (required_bytes + margin)
        except Exception as e:
            logger.warning(f"Could not check disk usage: {e}")
            return True

    @staticmethod
    def deduplicate_arcname(arcname: str, existing_names: set) -> str:
        """Ensure no duplicate file paths exist inside the ZIP archive."""
        if arcname not in existing_names:
            existing_names.add(arcname)
            return arcname

        parent = os.path.dirname(arcname)
        base = os.path.basename(arcname)
        parts = base.rsplit(".", 1)
        name = parts[0]
        ext = f".{parts[1]}" if len(parts) == 2 else ""

        counter = 1
        while True:
            candidate = f"{name} ({counter}){ext}"
            new_arcname = os.path.join(parent, candidate).replace("\\", "/") if parent else candidate
            if new_arcname not in existing_names:
                existing_names.add(new_arcname)
                return new_arcname
            counter += 1

    @classmethod
    def create_zip_archive(
        cls,
        items: List[ArchiveItem],
        archive_name: str,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Package files into a compressed ZIP archive.
        Preserves folder hierarchy, Unicode characters, and safely deduplicates filenames.
        """
        out_dir = output_dir or settings.ARCHIVE_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        clean_name = sanitize_filename(archive_name)
        if not clean_name.lower().endswith(".zip"):
            clean_name += ".zip"

        archive_path = out_dir / clean_name

        # Calculate estimated size
        total_size = sum(item.local_path.stat().st_size for item in items if item.local_path.exists())
        if not cls.check_free_space(total_size, out_dir):
            raise IOError(f"Insufficient disk space to create archive '{clean_name}' ({total_size} bytes required).")

        existing_arcnames: set = set()

        logger.info(f"Creating ZIP archive '{archive_path}' with {len(items)} files...")
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                if not item.local_path.exists() or not item.local_path.is_file():
                    logger.warning(f"File '{item.local_path}' does not exist, skipping in archive.")
                    continue

                safe_arcname = cls.deduplicate_arcname(item.arcname.replace("\\", "/"), existing_arcnames)
                zf.write(item.local_path, arcname=safe_arcname)

        logger.info(f"ZIP archive created successfully at '{archive_path}' (size: {archive_path.stat().st_size} bytes)")
        return archive_path

    @classmethod
    def create_zip_from_directory(cls, source_dir: Path, archive_name: str) -> Path:
        """Pack an entire directory tree into a ZIP archive."""
        items: List[ArchiveItem] = []
        for root, _, files in os.walk(source_dir):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(source_dir)
                items.append(ArchiveItem(local_path=full_path, arcname=str(rel_path)))

        return cls.create_zip_archive(items, archive_name)
