import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse
from utils.logger import logger
from utils.validators import check_ssrf, is_terabox_domain, TERABOX_URL_PATTERN


@dataclass
class ParsedTeraBoxLink:
    raw_url: str
    clean_url: str
    surl: str  # Short URL key (without leading '1' when stripped, or raw key)
    raw_surl: str  # Original key as found in URL (e.g. '1abcd')
    domain: str
    password: Optional[str] = None


class LinkParser:
    """Parses and validates TeraBox share links from user text messages."""

    # Regex to detect password / extraction code attached in query string or message text
    PWD_REGEX = re.compile(r"(?:pwd|code|password)[:=\s]+([a-zA-Z0-9]{4,8})", re.IGNORECASE)

    @classmethod
    def extract_links_from_text(cls, text: str) -> List[str]:
        """Extract all raw TeraBox URLs from arbitrary text message."""
        if not text:
            return []
        matches = TERABOX_URL_PATTERN.findall(text)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for match in matches:
            clean = match.rstrip(".,;)>]\"'")
            if clean not in seen:
                seen.add(clean)
                unique.append(clean)
        return unique

    @classmethod
    def parse_link(cls, raw_url: str, fallback_pwd: Optional[str] = None) -> Optional[ParsedTeraBoxLink]:
        """
        Parse and validate a single TeraBox link.
        Returns ParsedTeraBoxLink or None if invalid or SSRF check fails.
        """
        try:
            parsed = urlparse(raw_url)
            domain = (parsed.hostname or "").lower()

            if not is_terabox_domain(domain):
                logger.warning(f"Domain '{domain}' is not recognized as a valid TeraBox domain.")
                return None

            # SSRF validation on the host
            if not check_ssrf(raw_url):
                logger.warning(f"SSRF check failed for URL: {raw_url}")
                return None

            raw_surl = ""
            # Case 1: /s/1xxxxxxxx or /s/xxxxxxxx
            path_parts = [p for p in parsed.path.split("/") if p]
            if "s" in path_parts:
                idx = path_parts.index("s")
                if idx + 1 < len(path_parts):
                    raw_surl = path_parts[idx + 1]

            # Case 2: Query param ?surl=xxxx or ?shorturl=xxxx
            query_params = parse_qs(parsed.query)
            if not raw_surl:
                if "surl" in query_params:
                    raw_surl = query_params["surl"][0]
                elif "shorturl" in query_params:
                    raw_surl = query_params["shorturl"][0]

            if not raw_surl:
                logger.warning(f"Could not extract surl/shorturl from: {raw_url}")
                return None

            # Extract password / extraction code if present
            password = fallback_pwd
            if "pwd" in query_params:
                password = query_params["pwd"][0]
            elif "password" in query_params:
                password = query_params["password"][0]

            # Canonical surl: TeraBox internal API parameters often strip the leading '1'
            # if length is 23 or starts with '1'
            canonical_surl = raw_surl[1:] if raw_surl.startswith("1") and len(raw_surl) > 1 else raw_surl

            clean_url = f"https://{domain}/s/{raw_surl}"
            if password:
                clean_url += f"?pwd={password}"

            return ParsedTeraBoxLink(
                raw_url=raw_url,
                clean_url=clean_url,
                surl=canonical_surl,
                raw_surl=raw_surl,
                domain=domain,
                password=password,
            )

        except Exception as e:
            logger.error(f"Error parsing TeraBox link '{raw_url}': {e}")
            return None

    @classmethod
    def parse_all(cls, text: str) -> List[ParsedTeraBoxLink]:
        """Extract and parse all TeraBox links from input text."""
        raw_urls = cls.extract_links_from_text(text)
        results = []

        # Check if message contains an extraction code pattern
        global_pwd_match = cls.PWD_REGEX.search(text)
        fallback_pwd = global_pwd_match.group(1) if global_pwd_match else None

        for url in raw_urls:
            parsed = cls.parse_link(url, fallback_pwd=fallback_pwd)
            if parsed:
                results.append(parsed)

        return results
