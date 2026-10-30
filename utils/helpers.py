import html
import re
from typing import Union


def format_size(size_bytes: Union[int, float]) -> str:
    """Format bytes into human-readable units (B, KB, MB, GB, TB)."""
    if size_bytes is None or size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    unit_idx = 0
    while size >= 1024.0 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1
    return f"{size:.2f} {units[unit_idx]}" if unit_idx > 0 else f"{int(size)} B"


def format_speed(bytes_per_sec: Union[int, float]) -> str:
    """Format speed in human-readable units per second."""
    return f"{format_size(bytes_per_sec)}/s"


def format_seconds(seconds: Union[int, float]) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def escape_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    if not text:
        return ""
    return html.escape(str(text))


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """
    Sanitize filename for cross-platform compatibility (Windows & Linux).
    Preserves Unicode and valid file extensions.
    """
    if not filename:
        return "unnamed_file"

    # Replace forbidden filesystem characters: < > : " / \ | ? *
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename).strip()

    # Prevent names like '.' or '..' or trailing dots/spaces on Windows
    clean = clean.strip(". ")
    if not clean:
        clean = "file"

    if len(clean) > max_length:
        parts = clean.rsplit(".", 1)
        if len(parts) == 2:
            base, ext = parts
            clean = base[: max_length - len(ext) - 1] + "." + ext
        else:
            clean = clean[:max_length]

    return clean


def generate_progress_bar(percentage: float, length: int = 10) -> str:
    """Generate a clean visual progress bar."""
    pct = max(0.0, min(100.0, float(percentage)))
    filled_len = int(round(length * (pct / 100.0)))
    empty_len = length - filled_len
    bar = "▰" * filled_len + "▱" * empty_len
    return f"[{bar}] {pct:.1f}%"
