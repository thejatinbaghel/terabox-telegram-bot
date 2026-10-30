import zipfile
from pathlib import Path
import pytest
from services.archive_manager import ArchiveItem, ArchiveManager


def test_archive_creation_and_unicode(tmp_path: Path):
    # Setup sample files
    src_dir = tmp_path / "files"
    src_dir.mkdir()

    f1 = src_dir / "document_中文.txt"
    f1.write_text("Hello Chinese Unicode", encoding="utf-8")

    f2 = src_dir / "folder_a"
    f2.mkdir()
    f3 = f2 / "subfile.txt"
    f3.write_text("Nested subfile content", encoding="utf-8")

    items = [
        ArchiveItem(local_path=f1, arcname="document_中文.txt"),
        ArchiveItem(local_path=f3, arcname="folder_a/subfile.txt"),
    ]

    out_dir = tmp_path / "output_archives"
    zip_path = ArchiveManager.create_zip_archive(items, "test_archive", output_dir=out_dir)

    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    # Verify inside zip
    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        assert "document_中文.txt" in namelist
        assert "folder_a/subfile.txt" in namelist
        assert zf.read("document_中文.txt").decode("utf-8") == "Hello Chinese Unicode"


def test_archive_duplicate_filename_handling(tmp_path: Path):
    src_dir = tmp_path / "files"
    src_dir.mkdir()

    f1 = src_dir / "file1.txt"
    f1.write_text("First file", encoding="utf-8")

    f2 = src_dir / "file2.txt"
    f2.write_text("Second file with duplicate name", encoding="utf-8")

    items = [
        ArchiveItem(local_path=f1, arcname="duplicate.txt"),
        ArchiveItem(local_path=f2, arcname="duplicate.txt"),
    ]

    out_dir = tmp_path / "output_archives"
    zip_path = ArchiveManager.create_zip_archive(items, "dedup_test", output_dir=out_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        assert "duplicate.txt" in namelist
        assert "duplicate (1).txt" in namelist
