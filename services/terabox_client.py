import asyncio
import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import urllib.parse
import aiohttp
from config.settings import settings
from services.content_detector import ContentDetector, FileCategory, TeraBoxItem
from services.link_parser import LinkParser, ParsedTeraBoxLink
from utils.logger import logger


@dataclass
class ShareInfoResult:
    success: bool
    errno: int
    errmsg: str
    items: List[TeraBoxItem]
    shareid: Optional[str] = None
    uk: Optional[str] = None
    title: Optional[str] = None
    current_dir: str = "/"


class TeraBoxError(Exception):
    def __init__(self, message: str, errno: int = -1):
        super().__init__(message)
        self.errno = errno


class TeraBoxClient:
    """
    Dedicated client for querying TeraBox shared files and retrieving direct download links.
    Handles session cookies (ndus), directory navigation, and error reporting.
    """

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, cookie: Optional[str] = None, base_api_url: Optional[str] = None):
        self.cookie = cookie or settings.TERABOX_COOKIE
        self.base_api_url = base_api_url or settings.TERABOX_API_URL

    def _get_headers(self, domain: str, surl: str) -> Dict[str, str]:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://{domain}/sharing/link?surl={surl}",
        }
        if self.cookie:
            # Check if cookie already contains 'ndus='
            if "ndus=" in self.cookie:
                headers["Cookie"] = self.cookie
            else:
                headers["Cookie"] = f"lang=en; ndus={self.cookie}"
        return headers

    async def get_share_info(
        self,
        link_or_surl: str,
        password: Optional[str] = None,
        dir_path: str = "/",
        page: int = 1,
        page_size: int = 100,
    ) -> ShareInfoResult:
        """
        Fetch files and directories inside a TeraBox share link.
        Supports root and subfolder listing.
        """
        # Parse link if full URL passed
        if link_or_surl.startswith("http"):
            parsed = LinkParser.parse_link(link_or_surl, fallback_pwd=password)
            if not parsed:
                return ShareInfoResult(
                    success=False,
                    errno=-1,
                    errmsg="Invalid TeraBox URL or failed security verification.",
                    items=[],
                )
            domain = parsed.domain
            surl = parsed.surl
            raw_surl = parsed.raw_surl
            pwd = parsed.password or password
        else:
            domain = "www.terabox.app"
            raw_surl = link_or_surl
            surl = link_or_surl[1:] if link_or_surl.startswith("1") and len(link_or_surl) > 1 else link_or_surl
            pwd = password

        # 1. Custom API resolver fallback if configured
        if self.base_api_url:
            try:
                return await self._fetch_from_custom_api(self.base_api_url, surl, pwd, dir_path)
            except Exception as e:
                logger.warning(f"Custom resolver failed, falling back to direct endpoints: {e}")

        # 2. Direct TeraBox web endpoint
        endpoints = [
            f"https://www.terabox.app/share/list",
            f"https://dm.terabox.app/share/list",
            f"https://{domain}/share/list",
            f"https://1024tera.com/share/list",
            f"https://{domain}/api/shorturlinfo",
        ]

        params = {
            "app_id": "250528",
            "shorturl": surl,
            "root": "1" if dir_path == "/" else "0",
            "page": str(page),
            "num": str(page_size),
        }
        if dir_path != "/":
            params["dir"] = dir_path
        if pwd:
            params["pwd"] = pwd

        headers = self._get_headers(domain, raw_surl)

        timeout = aiohttp.ClientTimeout(total=25)
        last_error = "Failed to connect to TeraBox servers"
        last_errno = -1

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for endpoint in endpoints:
                try:
                    logger.info(f"Querying TeraBox API endpoint: {endpoint} (surl={surl}, dir={dir_path})")
                    async with session.get(endpoint, params=params, headers=headers) as resp:
                        if resp.status != 200:
                            logger.warning(f"Endpoint {endpoint} returned HTTP status {resp.status}")
                            continue

                        data = await resp.json(content_type=None)
                        if not isinstance(data, dict):
                            continue

                        errno = data.get("errno", 0)
                        if errno != 0:
                            errmsg = self._map_errno_to_message(errno)
                            logger.warning(f"TeraBox endpoint {endpoint} returned errno {errno}: {errmsg}")
                            last_errno = errno
                            last_error = errmsg
                            # If password is required, do not continue querying other endpoints
                            if errno in (110, -9):
                                return ShareInfoResult(
                                    success=False,
                                    errno=errno,
                                    errmsg=errmsg,
                                    items=[],
                                    current_dir=dir_path,
                                )
                            continue

                        shareid = str(data.get("shareid") or data.get("share_id") or "")
                        uk = str(data.get("uk") or "")

                        raw_list = data.get("list", [])
                        items: List[TeraBoxItem] = []
                        for entry in raw_list:
                            filename = entry.get("server_filename") or entry.get("filename") or "unnamed"
                            raw_isdir = entry.get("isdir", 0)
                            is_dir = str(raw_isdir).strip() == "1"
                            size = int(entry.get("size", 0))
                            fs_id = str(entry.get("fs_id", ""))
                            path = entry.get("path") or f"/{filename}"
                            dlink = entry.get("dlink")
                            thumbs = entry.get("thumbs", {})
                            thumb_url = thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1")

                            # Extract signed credentials from thumb_url if available
                            sign = None
                            timestamp = None
                            for t_url in [thumbs.get("url1"), thumbs.get("url2"), thumbs.get("url3")]:
                                if t_url:
                                    if "sign=" in t_url and not sign:
                                        try:
                                            sign = urllib.parse.unquote(t_url.split("sign=")[1].split("&")[0])
                                        except Exception:
                                            pass
                                    if "time=" in t_url and not timestamp:
                                        try:
                                            timestamp = t_url.split("time=")[1].split("&")[0]
                                        except Exception:
                                            pass
                                    if sign and timestamp:
                                        break

                            category = ContentDetector.detect_category(filename, is_dir=is_dir)
                            items.append(
                                TeraBoxItem(
                                    fs_id=fs_id,
                                    filename=filename,
                                    size=size,
                                    is_dir=is_dir,
                                    path=path,
                                    category=category,
                                    dlink=dlink,
                                    thumbnail=thumb_url,
                                    sign=sign,
                                    timestamp=timestamp,
                                    shareid=shareid,
                                    uk=uk,
                                )
                            )

                        title = data.get("title") or (items[0].filename if items else "Shared Folder")

                        return ShareInfoResult(
                            success=True,
                            errno=0,
                            errmsg="Success",
                            items=items,
                            shareid=shareid,
                            uk=uk,
                            title=title,
                            current_dir=dir_path,
                        )

                except asyncio.TimeoutError:
                    last_error = "Connection to TeraBox timed out. Please try again."
                    logger.warning(f"Timeout querying {endpoint}")
                except Exception as e:
                    last_error = f"Error querying TeraBox: {str(e)}"
                    logger.warning(f"Error querying {endpoint}: {e}")

        return ShareInfoResult(
            success=False,
            errno=last_errno,
            errmsg=last_error,
            items=[],
            current_dir=dir_path,
        )

    def _map_errno_to_message(self, errno: int) -> str:
        """Provide friendly, helpful error explanations for common TeraBox error codes."""
        mapping = {
            -1: "TeraBox session expired or requires authentication. Please configure TERABOX_COOKIE.",
            -7: "This TeraBox share link has expired or has been deleted by its owner.",
            -9: "Access denied. Password protected link or unauthorized access.",
            110: "This link is password protected. Please provide the extraction code (e.g. ?pwd=XXXX).",
            400210: "TeraBox rate-limit or IP security check triggered. Configure TERABOX_COOKIE (ndus).",
            404: "Content not found on TeraBox servers.",
        }
        return mapping.get(errno, f"TeraBox returned error code {errno}.")

    async def _fetch_from_custom_api(
        self, api_url: str, surl: str, pwd: Optional[str], dir_path: str
    ) -> ShareInfoResult:
        """Call a custom proxy or external resolver if configured."""
        payload = {"surl": surl, "pwd": pwd, "dir": dir_path}
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
                raw_list = data.get("list", [])
                items = [
                    TeraBoxItem(
                        fs_id=str(x.get("fs_id", "")),
                        filename=x.get("filename", "unnamed"),
                        size=int(x.get("size", 0)),
                        is_dir=str(x.get("isdir", "0")).strip() == "1",
                        path=x.get("path", "/"),
                        category=ContentDetector.detect_category(x.get("filename", ""), str(x.get("isdir", "0")).strip() == "1"),
                        dlink=x.get("dlink"),
                    )
                    for x in raw_list
                ]
                return ShareInfoResult(
                    success=True,
                    errno=0,
                    errmsg="Success",
                    items=items,
                    shareid=str(data.get("shareid", "")),
                    uk=str(data.get("uk", "")),
                    title=data.get("title", "Files"),
                    current_dir=dir_path,
                )

    # Class-level cache for extracted jsTokens
    _js_token_cache: Dict[str, str] = {}

    async def get_js_token(self, link_or_surl: str) -> Optional[str]:
        """Extract and cache the window.jsToken embedded in the TeraBox sharing page."""
        surl = link_or_surl
        if link_or_surl.startswith("http"):
            parsed = LinkParser.parse_link(link_or_surl)
            if parsed:
                surl = parsed.surl
        else:
            if surl.startswith("1") and len(surl) > 1:
                surl = surl[1:]

        if surl in self._js_token_cache:
            return self._js_token_cache[surl]

        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie if "ndus=" in self.cookie else f"lang=en; ndus={self.cookie}"

        domains = ["www.terabox.app", "1024terabox.com", "terabox.app", "1024tera.com"]
        for d in domains:
            page_url = f"https://{d}/sharing/link?surl={surl}"
            headers["Referer"] = page_url
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                    async with session.get(page_url, headers=headers) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            matches = re.findall(r'fn%28%22([A-F0-9]+)%22%29', html)
                            if matches:
                                token = matches[0]
                                self._js_token_cache[surl] = token
                                return token
                            m2 = re.search(r'window\.jsToken\s*=\s*[\'"]([A-F0-9]+)[\'"]', html)
                            if m2:
                                token = m2.group(1)
                                self._js_token_cache[surl] = token
                                return token
            except Exception as e:
                logger.debug(f"Could not extract jsToken from {page_url}: {e}")

        return None

    @staticmethod
    def generate_streaming_signature(
        browserid: str,
        timestamp: str,
        clienttype: str = "0",
        channel: str = "dubox",
    ) -> str:
        """
        Generates the official HMAC-SHA1 signature required by TeraBox's /share/streaming endpoint.
        TeraBox web frontend implementation:
            mo("".concat(clienttype).concat(channel).concat(browserid).concat(timestamp))
        where mo = HMAC-SHA1(msg, "iuuPc64E4Fhn0rTXEzrnbLph0o5qyEEa")
        """
        key = b"iuuPc64E4Fhn0rTXEzrnbLph0o5qyEEa"
        msg = f"{clienttype}{channel}{browserid}{timestamp}".encode("utf-8")
        return hmac.new(key, msg, hashlib.sha1).hexdigest()

    async def get_video_stream_segments(
        self, link_or_surl: str, item: TeraBoxItem
    ) -> Optional[List[str]]:
        """
        Fetches the complete M3U8 streaming playlist for a video file on TeraBox.
        Generates authorized HMAC-SHA1 signatures so that TeraBox delivers the
        full video duration rather than a 30-second trial preview.
        Returns the ordered list of HTTP URLs for all TS video segments.
        """
        surl = link_or_surl
        if link_or_surl.startswith("http"):
            parsed = LinkParser.parse_link(link_or_surl)
            if parsed:
                surl = parsed.surl
        else:
            if surl.startswith("1") and len(surl) > 1:
                surl = surl[1:]

        # Ensure shareid and uk are available on the item
        if not item.shareid or not item.uk:
            parent_dir = str(Path(item.path).parent).replace("\\", "/") if item.path else "/"
            info = await self.get_share_info(link_or_surl, dir_path=parent_dir)
            if not info.success or not info.shareid:
                info = await self.get_share_info(link_or_surl, dir_path="/")

            if info.success:
                item.shareid = item.shareid or info.shareid
                item.uk = item.uk or info.uk
                for it in info.items:
                    if it.fs_id == item.fs_id:
                        item.shareid = it.shareid or item.shareid
                        item.uk = it.uk or item.uk
                        break

        if not item.shareid or not item.uk or not item.fs_id:
            logger.warning(f"Missing required metadata (shareid/uk/fid) for item '{item.filename}'")
            return None

        js_token = await self.get_js_token(surl) or ""
        browserid = base64.b64encode(os.urandom(32)).decode("ascii") + "="
        timestamp = str(int(time.time()))
        sign = self.generate_streaming_signature(browserid, timestamp)

        cookie_str = self.cookie if "ndus=" in (self.cookie or "") else f"lang=en; ndus={self.cookie}"
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Referer": f"https://terabox.app/sharing/link?surl={surl}",
            "Cookie": f"{cookie_str}; browserid={browserid}",
        }

        endpoints = [
            "https://dm.terabox.app/share/streaming",
            "https://dm.1024tera.com/share/streaming",
            "https://www.terabox.app/share/streaming",
        ]
        qualities = ["M3U8_FLV_264_480", "M3U8_AUTO_720", "M3U8_AUTO_480", "M3U8_AUTO_360", "M3U8_AUTO_1080"]

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            for ep in endpoints:
                for q in qualities:
                    params = {
                        "uk": str(item.uk),
                        "shareid": str(item.shareid),
                        "type": q,
                        "fid": str(item.fs_id),
                        "sign": sign,
                        "timestamp": timestamp,
                        "jsToken": js_token,
                        "esl": "1",
                        "isplayer": "1",
                        "ehps": "1",
                        "clienttype": "0",
                        "app_id": "250528",
                        "web": "1",
                        "channel": "dubox",
                    }
                    try:
                        async with session.get(ep, params=params, headers=headers) as resp:
                            if resp.status == 200:
                                body = await resp.text()
                                if body.startswith("#EXTM3U"):
                                    segment_urls = [
                                        line.strip()
                                        for line in body.split("\n")
                                        if line.strip().startswith("http")
                                    ]
                                    if segment_urls:
                                        inf_matches = re.findall(r"#EXTINF:([0-9.]+)", body)
                                        dur_sec = sum(float(x) for x in inf_matches)
                                        logger.info(
                                            f"Successfully resolved full video stream: {len(segment_urls)} segments "
                                            f"({dur_sec:.1f}s) with quality {q} for '{item.filename}'"
                                        )
                                        return segment_urls
                    except Exception as e:
                        logger.debug(f"Streaming request failed at {ep} for quality {q}: {e}")

        logger.warning(f"Could not obtain full M3U8 video stream for '{item.filename}'")
        return None

    async def get_all_files_recursive(
        self,
        link_or_surl: str,
        root_dir: str = "/",
        password: Optional[str] = None,
    ) -> List[Tuple[TeraBoxItem, str]]:
        """
        Recursively traverses all files and subdirectories.
        Returns a list of tuples: (TeraBoxItem, relative_path)
        """
        all_files: List[Tuple[TeraBoxItem, str]] = []
        dirs_to_visit: List[Tuple[str, str]] = [(root_dir, "")]  # (terabox_dir_path, relative_prefix)

        visited_dirs: set[str] = set()

        while dirs_to_visit:
            curr_dir, rel_prefix = dirs_to_visit.pop(0)
            if curr_dir in visited_dirs:
                continue
            visited_dirs.add(curr_dir)

            res = await self.get_share_info(link_or_surl, password=password, dir_path=curr_dir)
            if not res.success:
                logger.warning(f"Failed to list directory '{curr_dir}': {res.errmsg}")
                continue

            for it in res.items:
                if it.is_dir:
                    sub_prefix = f"{rel_prefix}/{it.filename}" if rel_prefix else it.filename
                    dirs_to_visit.append((it.path, sub_prefix))
                else:
                    rel_path = f"{rel_prefix}/{it.filename}" if rel_prefix else it.filename
                    all_files.append((it, rel_path))

        return all_files
