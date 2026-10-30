import pytest
from services.content_detector import ContentDetector, ContentScenario, FileCategory, TeraBoxItem
from services.file_browser import BrowserSessionManager
from services.link_parser import LinkParser


def test_link_parser_single_and_pwd():
    text = "Here is the file https://www.terabox.app/s/1abcdEFG123456?pwd=k89a"
    links = LinkParser.parse_all(text)
    assert len(links) == 1
    assert links[0].raw_surl == "1abcdEFG123456"
    assert links[0].surl == "abcdEFG123456"  # Strips leading '1'
    assert links[0].password == "k89a"


def test_link_parser_multi_links():
    text = (
        "Check these two links:\n"
        "1. https://terabox.com/s/1FirstShareKey\n"
        "2. https://1024tera.com/sharing/link?surl=SecondShareKey"
    )
    links = LinkParser.parse_all(text)
    assert len(links) == 2
    assert links[0].raw_surl == "1FirstShareKey"
    assert links[1].raw_surl == "SecondShareKey"


def test_scenario_a_single_video():
    items = [
        TeraBoxItem(
            fs_id="101",
            filename="tutorial_clip.mp4",
            size=104857600,
            is_dir=False,
            path="/tutorial_clip.mp4",
            category=FileCategory.VIDEO,
        )
    ]
    analysis = ContentDetector.analyze(items)
    assert analysis.scenario == ContentScenario.SINGLE_VIDEO
    assert analysis.total_files == 1
    assert analysis.total_folders == 0
    assert analysis.category_counts[FileCategory.VIDEO] == 1


def test_scenario_b_multiple_videos():
    items = [
        TeraBoxItem(
            fs_id="101",
            filename="episode_1.mp4",
            size=104857600,
            is_dir=False,
            path="/episode_1.mp4",
            category=FileCategory.VIDEO,
        ),
        TeraBoxItem(
            fs_id="102",
            filename="episode_2.mkv",
            size=209715200,
            is_dir=False,
            path="/episode_2.mkv",
            category=FileCategory.VIDEO,
        ),
    ]
    analysis = ContentDetector.analyze(items)
    assert analysis.scenario == ContentScenario.MULTIPLE_VIDEOS
    assert analysis.total_files == 2
    assert analysis.category_counts[FileCategory.VIDEO] == 2


def test_scenario_c_multiple_images():
    items = [
        TeraBoxItem(
            fs_id="201",
            filename="photo_1.jpg",
            size=5242880,
            is_dir=False,
            path="/photo_1.jpg",
            category=FileCategory.IMAGE,
        ),
        TeraBoxItem(
            fs_id="202",
            filename="photo_2.png",
            size=3145728,
            is_dir=False,
            path="/photo_2.png",
            category=FileCategory.IMAGE,
        ),
    ]
    analysis = ContentDetector.analyze(items)
    assert analysis.scenario == ContentScenario.MULTIPLE_IMAGES
    assert analysis.category_counts[FileCategory.IMAGE] == 2


def test_scenario_d_mixed_files():
    items = [
        TeraBoxItem(
            fs_id="301",
            filename="track.mp3",
            size=10000000,
            is_dir=False,
            path="/track.mp3",
            category=FileCategory.AUDIO,
        ),
        TeraBoxItem(
            fs_id="302",
            filename="document.pdf",
            size=2000000,
            is_dir=False,
            path="/document.pdf",
            category=FileCategory.DOCUMENT,
        ),
        TeraBoxItem(
            fs_id="303",
            filename="archive.zip",
            size=50000000,
            is_dir=False,
            path="/archive.zip",
            category=FileCategory.ARCHIVE,
        ),
    ]
    analysis = ContentDetector.analyze(items)
    assert analysis.scenario == ContentScenario.MIXED
    assert analysis.total_files == 3


def test_scenario_e_folders():
    items = [
        TeraBoxItem(
            fs_id="401",
            filename="Movies",
            size=0,
            is_dir=True,
            path="/Movies",
            category=FileCategory.FOLDER,
        ),
        TeraBoxItem(
            fs_id="402",
            filename="notes.txt",
            size=1024,
            is_dir=False,
            path="/notes.txt",
            category=FileCategory.DOCUMENT,
        ),
    ]
    analysis = ContentDetector.analyze(items)
    assert analysis.scenario == ContentScenario.FOLDERS
    assert analysis.total_folders == 1
    assert analysis.total_files == 1


def test_browser_session_selection_and_pagination():
    session_mgr = BrowserSessionManager()
    items = [
        TeraBoxItem(
            fs_id=f"f_{i}",
            filename=f"file_{i}.txt",
            size=1000 * i,
            is_dir=False,
            path=f"/file_{i}.txt",
            category=FileCategory.DOCUMENT,
        )
        for i in range(15)
    ]
    session = session_mgr.create_session(
        user_id=123,
        source_url="https://terabox.com/s/1demo",
        surl="demo",
        title="My Collection",
        items=items,
    )

    # Check pagination: 15 items with page_size=6 -> 3 pages
    assert session.total_pages() == 3
    page_1_items = session.get_current_page_items()
    assert len(page_1_items) == 6
    assert page_1_items[0][1].filename == "file_0.txt"

    # Multi-selection toggle
    session.selected_fs_ids.add("f_1")
    session.selected_fs_ids.add("f_2")
    assert len(session.selected_fs_ids) == 2
