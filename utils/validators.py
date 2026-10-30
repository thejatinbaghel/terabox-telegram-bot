import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Optional, Set
from urllib.parse import urlparse

# Supported TeraBox and mirror domains
TERABOX_DOMAINS: Set[str] = {
    "terabox.com",
    "www.terabox.com",
    "terabox.app",
    "www.terabox.app",
    "teraboxapp.com",
    "www.teraboxapp.com",
    "1024tera.com",
    "www.1024tera.com",
    "1024terabox.com",
    "www.1024terabox.com",
    "teraboxlink.com",
    "www.teraboxlink.com",
    "freeterabox.com",
    "www.freeterabox.com",
    "mirrobox.com",
    "www.mirrobox.com",
    "nephobox.com",
    "www.nephobox.com",
    "4funbox.com",
    "www.4funbox.com",
    "momerybox.com",
    "www.momerybox.com",
    "tibibox.com",
    "www.tibibox.com",
}

# Regex to detect TeraBox links
TERABOX_URL_PATTERN = re.compile(
    r"https?://(?:[a-zA-Z0-9-]+\.)*(?:terabox\.com|terabox\.app|teraboxapp\.com|1024tera\.com|1024terabox\.com|teraboxlink\.com|freeterabox\.com|mirrobox\.com|nephobox\.com|4funbox\.com|momerybox\.com|tibibox\.com)/[^\s]+",
    re.IGNORECASE,
)

PRIVATE_DOMAIN_SUFFIXES = {
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".test",
    ".invalid",
    ".localhost",
}


def is_terabox_domain(domain: str) -> bool:
    """Check if domain is a legitimate TeraBox domain or known mirror."""
    if not domain:
        return False
    domain = domain.lower().strip()
    return domain in TERABOX_DOMAINS or any(
        domain.endswith("." + td) for td in ["terabox.com", "terabox.app", "1024tera.com", "freeterabox.com"]
    )


def is_valid_url(url: str) -> bool:
    """Basic URL format validation."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is private, loopback, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Allow RFC 6052 Well-Known Prefix (64:ff9b::/96) used by IPv6 NAT64 transitions
        if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.IPv6Network("64:ff9b::/96"):
            return False
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def check_ssrf(url: str, allow_unresolved_public_domains: bool = True) -> bool:
    """
    Validate that the given URL does not resolve to an internal/private IP address.
    Guards against SSRF while allowing public domain names in offline/test environments.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        hostname_lower = hostname.lower()

        # Disallow localhost / local hostnames directly
        if hostname_lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False

        # Disallow private domain suffixes
        if any(hostname_lower.endswith(suffix) for suffix in PRIVATE_DOMAIN_SUFFIXES):
            return False

        # Disallow plain numeric/private IPs without DNS lookup
        if is_private_ip(hostname):
            return False

        # Resolve hostname to check actual destination IPs
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                if is_private_ip(ip_str):
                    return False
            return True
        except (socket.gaierror, socket.herror, OSError):
            # If DNS lookup fails (e.g. offline unit testing or container without internet)
            if allow_unresolved_public_domains:
                # Ensure it looks like a valid public FQDN (e.g. terabox.com, example.com)
                return bool(re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", hostname_lower))
            return False

    except Exception:
        return False


def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """
    Prevent directory traversal attacks by ensuring the target path is
    strictly inside the base directory.
    """
    try:
        base_resolved = base_dir.resolve()
        target_resolved = target_path.resolve()
        common = os.path.commonpath([str(base_resolved), str(target_resolved)])
        return common == str(base_resolved)
    except Exception:
        return False
