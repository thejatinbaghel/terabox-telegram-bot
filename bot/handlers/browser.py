from aiogram import F, Router
from aiogram.types import CallbackQuery
from services.content_detector import FileCategory, TeraBoxItem
from services.file_browser import FileBrowserUI, browser_sessions
from services.queue_manager import QueueManager
from services.terabox_client import TeraBoxClient
from utils.helpers import escape_html
from utils.logger import logger

router = Router(name="browser_router")


@router.callback_query(F.data.startswith("b:"))
async def handle_browser_callback(callback: CallbackQuery, queue_mgr: QueueManager):
    """Router for all inline file browser interactions."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid request.", show_alert=True)
        return

    sid = parts[1]
    action = parts[2]
    arg = parts[3] if len(parts) > 3 else None

    session = browser_sessions.get_session(sid)
    if not session:
        await callback.answer("⚠️ Browser session expired. Please resend the link.", show_alert=True)
        if callback.message:
            try:
                await callback.message.edit_text("⏳ <i>Browser session expired. Please send the link again.</i>", parse_mode="HTML")
            except Exception:
                pass
        return

    user_id = callback.from_user.id
    if user_id != session.user_id:
        await callback.answer("⚠️ This browser belongs to another user.", show_alert=True)
        return

    # 1. Close
    if action == "close":
        browser_sessions.remove_session(sid)
        if callback.message:
            await callback.message.delete()
        await callback.answer("Closed")
        return

    # 2. Pagination: p:<num>
    elif action == "p" and arg:
        try:
            page_num = int(arg)
            session.current_page = max(1, min(page_num, session.total_pages()))
            await _update_browser_view(callback, session)
        except ValueError:
            pass
        await callback.answer()
        return

    # 3. Category Filter: c:<cat>
    elif action == "c" and arg:
        if arg == "all":
            session.active_category = None
        else:
            try:
                session.active_category = FileCategory(arg)
            except ValueError:
                session.active_category = None
        session.current_page = 1
        await _update_browser_view(callback, session)
        await callback.answer()
        return

    # 4. Multi-selection: Toggle item t:<idx>
    elif action == "t" and arg:
        try:
            idx = int(arg)
            filtered = session.get_filtered_items()
            if 0 <= idx < len(filtered):
                target_item = filtered[idx]
                if not target_item.is_dir:
                    if target_item.fs_id in session.selected_fs_ids:
                        session.selected_fs_ids.remove(target_item.fs_id)
                    else:
                        session.selected_fs_ids.add(target_item.fs_id)
                    await _update_browser_view(callback, session)
        except ValueError:
            pass
        await callback.answer()
        return

    # 5. Select All / Deselect All
    elif action == "sel_all":
        filtered = session.get_filtered_items()
        for it in filtered:
            if not it.is_dir:
                session.selected_fs_ids.add(it.fs_id)
        await _update_browser_view(callback, session)
        await callback.answer(f"Selected {len(session.selected_fs_ids)} items")
        return

    elif action == "desel_all":
        session.selected_fs_ids.clear()
        await _update_browser_view(callback, session)
        await callback.answer("Selection cleared")
        return

    # 6. Open Folder: f:<idx>
    elif action == "f" and arg:
        try:
            idx = int(arg)
            filtered = session.get_filtered_items()
            if 0 <= idx < len(filtered) and filtered[idx].is_dir:
                folder_item = filtered[idx]
                target_path = folder_item.path

                await callback.answer("📂 Opening folder...")
                client = TeraBoxClient()
                res = await client.get_content_unwrapped(session.source_url, dir_path=target_path)

                if res.success:
                    session.current_dir = res.current_dir
                    session.dir_history.append(res.current_dir)
                    session.all_items = res.items
                    session.selected_fs_ids.clear()
                    session.current_page = 1
                    session.active_category = None
                    await _update_browser_view(callback, session)
                else:
                    await callback.answer(f"Could not open folder: {res.errmsg}", show_alert=True)
                return
        except ValueError:
            pass
        await callback.answer()
        return

    # 7. Back: Go to parent folder
    elif action == "back":
        if len(session.dir_history) > 1:
            session.dir_history.pop()  # pop current
            parent_dir = session.dir_history[-1]

            await callback.answer("⬅️ Loading...")
            client = TeraBoxClient()
            res = await client.get_share_info(session.source_url, dir_path=parent_dir)

            if res.success:
                session.current_dir = parent_dir
                session.all_items = res.items
                session.selected_fs_ids.clear()
                session.current_page = 1
                session.active_category = None
                await _update_browser_view(callback, session)
            else:
                await callback.answer(f"Error loading parent folder: {res.errmsg}", show_alert=True)
        else:
            await callback.answer("Already at root directory.")
        return

    # 8. Download Single Item: dl:<idx>
    elif action == "dl" and arg:
        try:
            idx = int(arg)
            filtered = session.get_filtered_items()
            if 0 <= idx < len(filtered):
                item = filtered[idx]
                try:
                    if item.is_dir:
                        # Folder item -> package as ZIP
                        folder_path = item.path if item.path else f"{session.current_dir.rstrip('/')}/{item.filename}"
                        clean_name = item.filename.strip("/").split("/")[-1].replace(".zip", "") or "folder"
                        job_id = await queue_mgr.add_job(
                            user_id=user_id,
                            source_url=session.source_url,
                            filename=f"{clean_name}.zip",
                            file_size=0,
                            category="folder",
                            dir_path=folder_path,
                        )
                        pos = await queue_mgr.get_queue_position(job_id)
                        await callback.answer(f"Folder ZIP enqueued: {clean_name} (Pos #{pos})", show_alert=True)
                    else:
                        job_id = await queue_mgr.add_job(
                            user_id=user_id,
                            source_url=session.source_url,
                            filename=item.filename,
                            file_size=item.size,
                            fs_id=item.fs_id,
                            category=item.category.value,
                            dir_path=session.current_dir,
                        )
                        pos = await queue_mgr.get_queue_position(job_id)
                        await callback.answer(f"Enqueued: {item.filename[:20]} (Pos #{pos})", show_alert=True)
                except Exception as e:
                    await callback.answer(f"Limit error: {str(e)}", show_alert=True)
                return
        except ValueError:
            pass
        await callback.answer()
        return

    # 9. Download Selected Items: dl_sel
    elif action == "dl_sel":
        if not session.selected_fs_ids:
            await callback.answer("No items selected. Tap files to select them.", show_alert=True)
            return

        selected_items = [it for it in session.all_items if it.fs_id in session.selected_fs_ids]
        enqueued = 0
        error_msg = None

        for it in selected_items:
            try:
                if it.is_dir:
                    folder_path = it.path if it.path else f"{session.current_dir.rstrip('/')}/{it.filename}"
                    clean_name = it.filename.strip("/").split("/")[-1].replace(".zip", "") or "folder"
                    await queue_mgr.add_job(
                        user_id=user_id,
                        source_url=session.source_url,
                        filename=f"{clean_name}.zip",
                        file_size=0,
                        category="folder",
                        dir_path=folder_path,
                    )
                else:
                    await queue_mgr.add_job(
                        user_id=user_id,
                        source_url=session.source_url,
                        filename=it.filename,
                        file_size=it.size,
                        fs_id=it.fs_id,
                        category=it.category.value,
                        dir_path=session.current_dir,
                    )
                enqueued += 1
            except Exception as e:
                error_msg = str(e)
                break

        session.selected_fs_ids.clear()
        await _update_browser_view(callback, session)

        msg = f"✅ Enqueued {enqueued} selected items."
        if error_msg:
            msg += f"\nNote: {error_msg}"
        await callback.answer(msg, show_alert=True)
        return

    # 10. Download All items in current folder/view: dl_all
    elif action == "dl_all":
        items_to_dl = session.all_items
        if not items_to_dl:
            await callback.answer("No items in this folder to download.", show_alert=True)
            return

        enqueued = 0
        error_msg = None
        for it in items_to_dl:
            try:
                if it.is_dir:
                    folder_path = it.path if it.path else f"{session.current_dir.rstrip('/')}/{it.filename}"
                    clean_name = it.filename.strip("/").split("/")[-1].replace(".zip", "") or "folder"
                    await queue_mgr.add_job(
                        user_id=user_id,
                        source_url=session.source_url,
                        filename=f"{clean_name}.zip",
                        file_size=0,
                        category="folder",
                        dir_path=folder_path,
                    )
                else:
                    await queue_mgr.add_job(
                        user_id=user_id,
                        source_url=session.source_url,
                        filename=it.filename,
                        file_size=it.size,
                        fs_id=it.fs_id,
                        category=it.category.value,
                        dir_path=session.current_dir,
                    )
                enqueued += 1
            except Exception as e:
                error_msg = str(e)
                break

        msg = f"✅ Enqueued {enqueued} items."
        if error_msg:
            msg += f"\nNote: {error_msg}"
        await callback.answer(msg, show_alert=True)
        return

    # 11. Download Folder as ZIP: dl_f
    elif action == "dl_f":
        folder_name = session.current_dir.strip("/").split("/")[-1] or session.title
        clean_name = folder_name.replace(".zip", "") or "folder"
        try:
            job_id = await queue_mgr.add_job(
                user_id=user_id,
                source_url=session.source_url,
                filename=f"{clean_name}.zip",
                file_size=0,
                category="folder",
                dir_path=session.current_dir,
            )
            pos = await queue_mgr.get_queue_position(job_id)
            await callback.answer(f"Folder ZIP enqueued: {clean_name} (Pos #{pos})", show_alert=True)
        except Exception as e:
            await callback.answer(f"Error: {str(e)}", show_alert=True)
        return

    await callback.answer()


async def _update_browser_view(callback: CallbackQuery, session):
    """Helper to refresh browser text and keyboard in place."""
    text = FileBrowserUI.render_text(session)
    keyboard = FileBrowserUI.build_keyboard(session)
    if callback.message:
        try:
            await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.debug(f"View update ignored (unchanged): {e}")


@router.callback_query(F.data == "noop")
async def on_noop_callback(callback: CallbackQuery):
    await callback.answer()
