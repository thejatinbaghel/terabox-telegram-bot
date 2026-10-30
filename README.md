# 🚀 Ultimate TeraBox Downloader Telegram Bot

A production-ready, high-performance, asynchronous Telegram bot that analyzes, downloads, and delivers TeraBox content directly to users. Built with **Python 3.13**, **aiogram 3.x**, **FastAPI**, **SQLAlchemy (Async)**, and **aiohttp**.

Supports single videos, multiple videos, photo albums, audio tracks, documents, archives, shared folders, nested hierarchies, and mixed content.

---

## 📑 Table of Contents

- [Key Features](#-key-features)
- [Architecture Overview](#-architecture-overview)
- [Intelligent Content Detection Scenarios](#-intelligent-content-detection-scenarios)
- [Interactive Inline File Browser](#-interactive-inline-file-browser)
- [Private Temporary Download Link Server](#-private-temporary-download-link-server)
- [Security & Anti-Abuse](#-security--anti-abuse)
- [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Local Installation](#local-installation)
  - [Configuring the TeraBox Session Cookie](#configuring-the-terabox-session-cookie)
- [Docker & VPS Deployment](#-docker--vps-deployment)
- [Telegram Bot Commands](#-telegram-bot-commands)
- [Admin Dashboard](#-admin-dashboard)
- [Automated Testing](#-automated-testing)
- [Configuration Reference](#-configuration-reference)
- [Telegram API Limits & Handling](#-telegram-api-limits--handling)
- [License](#-license)

---

## ✨ Key Features

- **⚡️ 100% Asynchronous & Non-Blocking:** High concurrency powered by `asyncio`, `aiogram 3`, and `aiohttp`.
- **🔍 Intelligent Scenario Detection:**
  - **Single Video:** Auto-enqueues and delivers with metadata, duration, and thumbnail.
  - **Multiple Videos / Images / Documents:** Launches an interactive inline file browser.
  - **Shared Folders & Subfolders:** Preserves folder hierarchy, breadcrumb navigation, and ZIP packaging.
  - **Batch Links:** Process multiple TeraBox links in a single message with 1-click batch enqueue.
- **📂 Interactive Telegram File Browser:**
  - Breadcrumb navigation (`Folder / Subfolder`).
  - Multi-file checkbox selection (`[x]` / `[ ]`, Select All, Deselect All).
  - Category filters (Videos, Images, Documents, etc.).
  - 64-byte callback safe session manager (no Telegram callback data overflow).
- **📦 Smart Folder Packaging (ZIP):**
  - Packages folders into compressed ZIP archives on-the-fly.
  - Preserves Unicode names and directory hierarchy.
  - Automatic duplicate filename disambiguation (`file (1).mp4`).
- **📥 Robust Download Manager:**
  - Resumes interrupted downloads using HTTP `Range` headers.
  - Low-memory chunked streaming (64KB chunks — never buffers entire files into RAM).
  - Smooth moving-average speed calculation, ETA, and progress bar.
  - Per-user and global concurrency limits.
  - Task cancellation via `asyncio.Event`.
- **🔗 Private Temporary Download Link Server:**
  - Built-in FastAPI server running on port 8080.
  - Delivers files exceeding Telegram upload limits via secure, unguessable tokens (`secrets.token_urlsafe(32)`).
  - Full HTTP Range support (chunked streaming, browser resumption, IDM/Aria2 compatible).
  - Configurable expiration time (default: 6 hours).
- **🛡 Security First:**
  - **SSRF Protection:** Resolves hostnames, blocks loopback, RFC 1918 private IPs, link-local, and reserved ranges.
  - **Path Traversal Protection:** Validates file boundaries using `os.path.commonpath`.
  - **Anti-Flood & Rate Limiting:** Built-in throttling middleware.
  - **Safe Logging:** Automatically masks bot tokens, cookies, and passwords in log files.
- **🧹 Automatic Storage Management:**
  - Background cleaner removes expired temporary links, abandoned `.part` files, and old archives.
  - Strict isolation: files currently used by active downloads are never deleted.
  - Disk space pre-flight checks before downloading or archiving.
- **👑 Full Admin Control Center:**
  - Telegram inline dashboard with CPU, RAM, disk usage, and queue metrics.
  - Pause/resume download queue.
  - Block/unblock users.
  - Broadcast announcements.
  - View failed jobs.

---

## 🏗 Architecture Overview

```
                        User Sends TeraBox Link
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │  LinkParser & SSRF   │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │    TeraBoxClient     │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │   ContentDetector    │
                       └───────────┬──────────┘
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         ▼                                                   ▼
┌──────────────────┐                               ┌──────────────────┐
│  Single Video    │                               │ Multiple Files / │
│  (Auto-Download) │                               │ Folders (Browser)│
└────────┬─────────┘                               └────────┬─────────┘
         │                                                   │
         └─────────────────────────┬─────────────────────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │     QueueManager     │
                       │ (User & Global Pool) │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │   DownloadManager    │
                       │  (Resume & Chunks)   │
                       └───────────┬──────────┘
                                   │
         ┌─────────────────────────┴─────────────────────────┐
         │                                                   │
         │ File <= 50MB                                      │ File > 50MB (or folder ZIP)
         ▼                                                   ▼
┌──────────────────┐                               ┌──────────────────┐
│  TelegramSender  │                               │ Temporary Link   │
│ (Video/Doc/Photo)│                               │ Server (FastAPI) │
└──────────────────┘                               └──────────────────┘
```

---

## 🎯 Intelligent Content Detection Scenarios

The bot automatically identifies the structure of the shared link and applies optimal handling:

| Scenario | Condition | Behavior |
| :--- | :--- | :--- |
| **Scenario A: Single Video** | Exactly 1 video file | Automatically enqueues for download; delivers as native streaming Telegram video with progress updates. |
| **Scenario B: Multiple Videos** | Multiple video files | Opens interactive file browser with individual download buttons, "Download All", and batch queueing. |
| **Scenario C: Multiple Images** | Multiple image files | Opens browser with image selection; delivers as Telegram media group album or individual photos. |
| **Scenario D: Mixed Content** | Videos, images, docs, audio | Categorizes files into filter tabs (🎬 Videos, 🖼 Images, 📄 Documents); supports selective download. |
| **Scenario E: Folders** | Shared or nested folders | Interactive breadcrumb navigation; supports downloading entire folder packaged as ZIP preserving structure. |
| **Multiple Links** | Multiple URLs in 1 message | Summarizes links into a single batch card with 1-click "Download All" or individual browse options. |

---

## 🖥 Interactive Inline File Browser

The inline keyboard browser respects Telegram's strict 64-byte callback limit using a lightweight server-side session store:

```
📦 TeraBox Content Found
Title: My Media Collection
Path: /Vacation/Photos

📊 Summary:
• Files: 12
• Folders: 2
• Total Size: 340.50 MB

☑️ Selected: 3 items (45.20 MB)

📄 Page 1 of 2
Select items or download directly below:

[🎬 (4)] [🖼 (8)] [📄 (2)]
[☑️ 🖼 photo_1.jpg (5.2 MB)] [⬇️]
[◻️ 🖼 photo_2.jpg (4.8 MB)] [⬇️]
[📁 Summer_Trip] [⬇️ ZIP]
[◀️ Prev] [1/2] [Next ▶️]
[☑️ Select All] [◻️ Deselect]
[⬇️ Download Selected (3)] [⬇️ Download All]
[⬅️ Back] [⬇️ This Folder (ZIP)] [❌ Close]
```

---

## 🔗 Private Temporary Download Link Server

For files that cannot or should not be sent directly via Telegram (e.g., files >50MB, user preference, or large ZIP archives), the bot generates an unguessable private download URL:

- **Token Generation:** Cryptographically secure 32-character tokens (`secrets.token_urlsafe(32)`).
- **HTTP Range Requests:** Full support for `206 Partial Content`, allowing pause/resume in Google Chrome, Firefox, IDM, and curl.
- **Anti-Traversal:** Path verification ensures files are strictly served from within authorized storage boundaries.
- **Configurable Expiry:** Files and tokens automatically expire (default: 6 hours) and are swept by the storage cleaner.

---

## 🛡 Security & Anti-Abuse

1. **SSRF Mitigation:**
   - Blocks private, loopback, and carrier-grade NAT IPs (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`).
   - Validates destination URLs before downloading.
2. **Directory Traversal Protection:**
   - Normalizes and verifies target paths against base directories using `os.path.commonpath`.
3. **Safe Secrets Logging:**
   - Redacts bot tokens, cookies (`ndus`), passwords, and session tokens from console and rotating log files.
4. **Admin Protection:**
   - Strictly validates incoming admin operations against configured `ADMIN_IDS`.
5. **Anti-Flood:**
   - Built-in rate limiting middleware to mitigate Telegram button spam.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.12+** (tested on Python 3.13)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/terabox-telegram-bot.git
   cd terabox-telegram-bot
   ```

2. **Create and activate a virtual environment:**
   ```bash
   # Windows
   python -m venv .venv
   .venv\Scripts\activate

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env
   ```
   Open `.env` in your editor and enter:
   - `BOT_TOKEN`: Your Telegram Bot token from @BotFather.
   - `ADMIN_IDS`: Your Telegram user ID (obtain from [@userinfobot](https://t.me/userinfobot)).
   - `TERABOX_COOKIE`: (Recommended) Your TeraBox `ndus` cookie.

5. **Run the bot:**
   ```bash
   python main.py
   ```

---

### Configuring the TeraBox Session Cookie

TeraBox restricts unauthenticated access to shared files. Passing a valid `ndus` session cookie ensures higher reliability, bypasses rate limits, and unlocks full speeds:

1. Log into your account on [terabox.com](https://www.terabox.com) in your web browser.
2. Press `F12` to open **Developer Tools**.
3. Go to the **Application** tab (Chrome/Edge) or **Storage** tab (Firefox).
4. In the left panel, expand **Cookies** and select `https://www.terabox.com`.
5. Locate the cookie named **`ndus`** and copy its value.
6. Paste the value into `.env`:
   ```ini
   TERABOX_COOKIE=ndus=YOUR_COPIED_NDUS_VALUE_HERE
   ```

---

## 🐳 Docker & VPS Deployment

A turnkey `Dockerfile` and `docker-compose.yml` with Redis and Nginx reverse proxy are included.

### Deploying with Docker Compose

1. **Prepare your `.env` file:**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Ensure `TEMP_SERVER_BASE_URL` matches your server's domain or public IP (e.g., `http://your-server-ip:8080` or `https://yourdomain.com`).

2. **Start the stack:**
   ```bash
   docker compose up -d --build
   ```

3. **Check logs:**
   ```bash
   docker compose logs -f bot
   ```

4. **Verify health:**
   ```bash
   curl http://localhost:8080/health
   ```

---

## 💬 Telegram Bot Commands

### User Commands

- `/start` - Welcome message and quick instructions.
- `/help` - In-depth guide on links, folders, and settings.
- `/about` - Technical details and architecture info.
- `/queue` - View active downloads and waiting queue position.
- `/history` - View past completed and failed downloads.
- `/settings` - Configure preferred delivery method (Direct Telegram vs Temporary Link).
- `/status` - Check current server limits and account quotas.
- `/cancel` - Stop all active and waiting downloads.

### Admin Commands

- `/admin` - Open the live Admin Control Dashboard.
- `/block <user_id>` - Block an abusive user.
- `/unblock <user_id>` - Unblock a user.
- `/broadcast <message>` - Broadcast an announcement to all registered bot users.

---

## 👑 Admin Dashboard

Access the interactive dashboard anytime using `/admin`:

- **Real-Time Stats:** Registered users, completed jobs, failed jobs, active downloads, waiting queue.
- **Hardware Metrics:** Live CPU %, RAM usage, and available disk space.
- **Queue Controls:** Pause or resume queue processing instantly with 1 click.
- **Storage Maintenance:** Manually trigger storage cleanup of expired files and old caches.
- **Failed Job Inspection:** View recent failures with exact reason and error logs.

---

## 🧪 Automated Testing

The project includes an end-to-end automated test suite covering:
- TeraBox URL parsing and password extraction
- Content detection scenarios (Single video, Multi-video, Albums, Folders, Mixed)
- Multi-link batch processing
- ZIP packaging with Unicode filenames and duplicate avoidance
- Streaming downloads, resume headers, and cancellation tokens
- Database repositories and concurrency enforcement
- Temporary link creation, HTTP Range requests, and expiry
- Storage manager disk cleanup without deleting active files
- SSRF and path traversal protection
- Admin authorization filters

Run the tests anytime:
```bash
python -m pytest tests/ -v
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
| :--- | :--- | :--- |
| `BOT_TOKEN` | *(Required)* | Telegram Bot token from @BotFather |
| `ADMIN_IDS` | `[]` | Comma-separated list of admin Telegram IDs |
| `DATABASE_URL` | `sqlite+aiosqlite:///./bot.db` | SQLAlchemy database URL (SQLite or PostgreSQL) |
| `REDIS_URL` | `None` | Optional Redis URL for queueing and caching |
| `DOWNLOAD_DIR` | `./storage/downloads` | Directory for active downloads |
| `ARCHIVE_DIR` | `./storage/archives` | Directory for generated ZIP archives |
| `MAX_FILE_SIZE_MB` | `2000` | Maximum file size allowed for processing |
| `MAX_TG_UPLOAD_SIZE_MB` | `50` | Maximum size sent directly via Telegram |
| `LOCAL_BOT_API_SERVER_URL` | `None` | Optional self-hosted Telegram Bot API Server URL |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Maximum simultaneous global downloads |
| `MAX_USER_ACTIVE_JOBS` | `2` | Maximum concurrent active tasks per user |
| `MAX_QUEUE_SIZE` | `100` | Maximum waiting queue capacity |
| `DOWNLOAD_RETRIES` | `3` | Retry attempts for transient download failures |
| `TEMP_SERVER_HOST` | `0.0.0.0` | Host for temporary download HTTP server |
| `TEMP_SERVER_PORT` | `8080` | Port for temporary download HTTP server |
| `TEMP_SERVER_BASE_URL` | `http://localhost:8080` | Public URL prefix for user download links |
| `TEMP_LINK_EXPIRY_HOURS` | `6` | Hours before temporary download links expire |
| `AUTO_DELETE_TEMP_FILES` | `true` | Automatically clean expired files from disk |
| `CLEANUP_INTERVAL_MINUTES`| `15` | Interval for background storage cleanup loop |
| `TERABOX_COOKIE` | `None` | TeraBox session cookie (`ndus=...`) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 📌 Telegram API Limits & Handling

- **Cloud Bot API Limit:** The default Telegram cloud Bot API restricts bots to sending files up to **50 MB**.
- **Automated Fallback:** When a file exceeds 50 MB, the bot seamlessly generates a **Private Temporary Download Link** and delivers a clean download card with resume support.
- **Local Bot API Server (Optional):** If you run your own [Local Telegram Bot API Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server), you can configure `LOCAL_BOT_API_SERVER_URL=http://localhost:8081` and `MAX_TG_UPLOAD_SIZE_MB=2000` to send files up to **2 GB** directly through Telegram.

---

## 📄 License

This project is licensed under the MIT License.
