import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from services.content_detector import ContentDetector, FileCategory, TeraBoxItem
from utils.helpers import escape_html, format_size


@dataclass
class BrowserSession:
    session_id: str
    user_id: int
    source_url: str
    surl: str
    title: str
    current_dir: str = "/"
    dir_history: List[str] = field(default_factory=lambda: ["/"])
    all_items: List[TeraBoxItem] = field(default_factory=list)
    selected_fs_ids: Set[str] = field(default_factory=set)
    current_page: int = 1
    page_size: int = 6
    active_category: Optional[FileCategory] = None
    created_at: float = field(default_factory=time.time)

    def get_filtered_items(self) -> List[TeraBoxItem]:
        if not self.active_category:
            return self.all_items
        return [i for i in self.all_items if i.category == self.active_category or i.is_dir]

    def total_pages(self) -> int:
        items = self.get_filtered_items()
        if not items:
            return 1
        return (len(items) + self.page_size - 1) // self.page_size

    def get_current_page_items(self) -> List[tuple[int, TeraBoxItem]]:
        """Returns list of (original_index, item) for the current page."""
        items = self.get_filtered_items()
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        return [(start + idx, item) for idx, item in enumerate(items[start:end])]


class BrowserSessionManager:
    """In-memory cache for interactive browser sessions with auto-expiry."""

    def __init__(self, ttl_seconds: int = 7200):
        self._sessions: Dict[str, BrowserSession] = {}
        self.ttl = ttl_seconds

    def create_session(
        self,
        user_id: int,
        source_url: str,
        surl: str,
        title: str,
        items: List[TeraBoxItem],
        current_dir: str = "/",
    ) -> BrowserSession:
        self.cleanup()
        session_id = uuid.uuid4().hex[:8]
        session = BrowserSession(
            session_id=session_id,
            user_id=user_id,
            source_url=source_url,
            surl=surl,
            title=title,
            current_dir=current_dir,
            dir_history=[current_dir],
            all_items=items,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        session = self._sessions.get(session_id)
        if session and (time.time() - session.created_at) < self.ttl:
            return session
        if session:
            self._sessions.pop(session_id, None)
        return None

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def cleanup(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.created_at >= self.ttl]
        for sid in expired:
            self._sessions.pop(sid, None)


browser_sessions = BrowserSessionManager()


class FileBrowserUI:
    """Builds interactive inline keyboard views and HTML text for browsing TeraBox content."""

    @staticmethod
    def render_text(session: BrowserSession) -> str:
        analysis = ContentDetector.analyze(session.all_items)
        crumbs = session.current_dir if session.current_dir != "/" else "Root"

        # Calculate selected size
        selected_items = [i for i in session.all_items if i.fs_id in session.selected_fs_ids]
        selected_size = sum(i.size for i in selected_items)

        text = (
            f"📦 <b>TeraBox Content Found</b>\n"
            f"<b>Title:</b> {escape_html(session.title)}\n"
            f"<b>Path:</b> <code>{escape_html(crumbs)}</code>\n\n"
            f"📊 <b>Summary:</b>\n"
            f"• Files: <code>{analysis.total_files}</code>\n"
            f"• Folders: <code>{analysis.total_folders}</code>\n"
            f"• Total Size: <code>{analysis.formatted_total_size}</code>\n"
        )

        if session.selected_fs_ids:
            text += (
                f"\n☑️ <b>Selected:</b> {len(session.selected_fs_ids)} items "
                f"({format_size(selected_size)})\n"
            )

        if session.active_category:
            text += f"\n🔍 <b>Filter:</b> {session.active_category.value.capitalize()}s only\n"

        text += (
            f"\n📄 <i>Page {session.current_page} of {session.total_pages()}</i>\n"
            f"Select items or download directly below:"
        )
        return text

    @staticmethod
    def build_keyboard(session: BrowserSession) -> InlineKeyboardMarkup:
        sid = session.session_id
        keyboard: List[List[InlineKeyboardButton]] = []

        # 1. Category Filter Buttons Row (only if items of that category exist)
        cat_counts = ContentDetector.analyze(session.all_items).category_counts
        cat_buttons: List[InlineKeyboardButton] = []
        if cat_counts[FileCategory.VIDEO] > 0:
            label = f"🎬 ({cat_counts[FileCategory.VIDEO]})"
            cat_buttons.append(InlineKeyboardButton(text=label, callback_data=f"b:{sid}:c:video"))
        if cat_counts[FileCategory.IMAGE] > 0:
            label = f"🖼 ({cat_counts[FileCategory.IMAGE]})"
            cat_buttons.append(InlineKeyboardButton(text=label, callback_data=f"b:{sid}:c:image"))
        if cat_counts[FileCategory.DOCUMENT] > 0:
            label = f"📄 ({cat_counts[FileCategory.DOCUMENT]})"
            cat_buttons.append(InlineKeyboardButton(text=label, callback_data=f"b:{sid}:c:document"))
        if cat_buttons:
            if session.active_category:
                cat_buttons.append(InlineKeyboardButton(text="🔄 Show All", callback_data=f"b:{sid}:c:all"))
            keyboard.append(cat_buttons[:4])

        # 2. File / Folder Item Rows
        page_items = session.get_current_page_items()
        for idx, item in page_items:
            is_selected = item.fs_id in session.selected_fs_ids
            check = "☑️ " if is_selected else ""
            if item.is_dir:
                # Folder row -> open folder or download folder
                name_disp = f"📁 {item.filename[:24]}"
                btn_open = InlineKeyboardButton(text=name_disp, callback_data=f"b:{sid}:f:{idx}")
                btn_dl = InlineKeyboardButton(text="⬇️ ZIP", callback_data=f"b:{sid}:dl:{idx}")
                keyboard.append([btn_open, btn_dl])
            else:
                # File row -> toggle selection + individual download button
                name_disp = f"{check}{item.icon} {item.filename[:20]} ({item.formatted_size})"
                btn_toggle = InlineKeyboardButton(text=name_disp, callback_data=f"b:{sid}:t:{idx}")
                btn_dl = InlineKeyboardButton(text="⬇️", callback_data=f"b:{sid}:dl:{idx}")
                keyboard.append([btn_toggle, btn_dl])

        # 3. Pagination Row
        tot_pages = session.total_pages()
        nav_row: List[InlineKeyboardButton] = []
        if session.current_page > 1:
            nav_row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"b:{sid}:p:{session.current_page - 1}"))
        nav_row.append(InlineKeyboardButton(text=f"{session.current_page}/{tot_pages}", callback_data="noop"))
        if session.current_page < tot_pages:
            nav_row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"b:{sid}:p:{session.current_page + 1}"))
        if len(nav_row) > 1:
            keyboard.append(nav_row)

        # 4. Multi-selection actions
        sel_row: List[InlineKeyboardButton] = []
        sel_row.append(InlineKeyboardButton(text="☑️ Select All", callback_data=f"b:{sid}:sel_all"))
        if session.selected_fs_ids:
            sel_row.append(InlineKeyboardButton(text="◻️ Deselect", callback_data=f"b:{sid}:desel_all"))
        keyboard.append(sel_row)

        # 5. Download Operations Row
        action_row: List[InlineKeyboardButton] = []
        if session.selected_fs_ids:
            action_row.append(
                InlineKeyboardButton(
                    text=f"⬇️ Download Selected ({len(session.selected_fs_ids)})",
                    callback_data=f"b:{sid}:dl_sel",
                )
            )
        action_row.append(InlineKeyboardButton(text="⬇️ Download All", callback_data=f"b:{sid}:dl_all"))
        keyboard.append(action_row)

        # 6. Folder Navigation / Controls
        bottom_row: List[InlineKeyboardButton] = []
        if len(session.dir_history) > 1:
            bottom_row.append(InlineKeyboardButton(text="⬅️ Back", callback_data=f"b:{sid}:back"))
        if session.current_dir != "/":
            bottom_row.append(InlineKeyboardButton(text="⬇️ This Folder (ZIP)", callback_data=f"b:{sid}:dl_f"))
        bottom_row.append(InlineKeyboardButton(text="❌ Close", callback_data=f"b:{sid}:close"))
        keyboard.append(bottom_row)

        return InlineKeyboardMarkup(inline_keyboard=keyboard)
