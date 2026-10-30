import pytest
from pathlib import Path
from unittest.mock import MagicMock
from bot.filters.admin_filter import AdminFilter
from config.settings import settings
from utils.validators import check_ssrf, is_safe_path, is_terabox_domain, is_valid_url


def test_terabox_domain_validation():
    assert is_terabox_domain("terabox.com") is True
    assert is_terabox_domain("www.terabox.app") is True
    assert is_terabox_domain("1024tera.com") is True
    assert is_terabox_domain("freeterabox.com") is True
    assert is_terabox_domain("mirrobox.com") is True
    assert is_terabox_domain("google.com") is False
    assert is_terabox_domain("evil-site.com") is False
    assert is_terabox_domain("") is False


def test_ssrf_protection():
    # Direct private & loopback IPs must be blocked
    assert check_ssrf("http://127.0.0.1/test") is False
    assert check_ssrf("http://localhost/test") is False
    assert check_ssrf("http://10.0.0.1/file") is False
    assert check_ssrf("http://192.168.1.1/admin") is False
    assert check_ssrf("http://169.254.169.254/latest/meta-data") is False
    assert check_ssrf("ftp://example.com") is False  # Non-HTTP protocol


def test_path_traversal_protection(tmp_path: Path):
    base_dir = tmp_path / "allowed_storage"
    base_dir.mkdir()

    safe_file = base_dir / "video.mp4"
    unsafe_traversal_1 = base_dir / ".." / "secret.txt"
    unsafe_traversal_2 = Path("/etc/passwd")

    assert is_safe_path(base_dir, safe_file) is True
    assert is_safe_path(base_dir, unsafe_traversal_1) is False
    assert is_safe_path(base_dir, unsafe_traversal_2) is False


@pytest.mark.asyncio
async def test_admin_filter_authorization():
    settings.ADMIN_IDS = [12345678, 87654321]
    admin_filter = AdminFilter()

    # Mock admin event
    admin_msg = MagicMock()
    admin_msg.from_user.id = 12345678
    assert await admin_filter(admin_msg) is True

    # Mock non-admin event
    unauth_msg = MagicMock()
    unauth_msg.from_user.id = 99999999
    assert await admin_filter(unauth_msg) is False
