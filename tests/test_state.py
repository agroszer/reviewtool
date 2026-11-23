"""Tests for ReviewSession caching, hide_viewed filter, and CLI selection."""

from pathlib import Path
from unittest.mock import MagicMock

from reviewtool.cli import build_arg_parser, select_frontend
from reviewtool.config import ConfigManager
from reviewtool.models import DiffHunk, DiffLine, FileDiff, FileEntry
from reviewtool.state import ReviewSession
from reviewtool.ui_tui.app import ReviewTuiApp
from reviewtool.ui_tui.diff_pane import DiffOptionList
from reviewtool.vcs.base import VCSBackend


def test_session_diff_cache_and_hide_viewed_filter(tmp_path: Path) -> None:
    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()

    vcs = MagicMock(spec=VCSBackend)
    vcs.repo_root = tmp_path
    vcs.name = "git"
    vcs.supports_amend = True
    vcs.get_branch_or_info.return_value = "main"

    e1 = FileEntry(path="a.py", status="M", staged=False)
    e2 = FileEntry(path="b.py", status="M", staged=False)
    vcs.scan_status.return_value = ([e1, e2], [])

    sample_diff = FileDiff(
        path="a.py",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(kind="add", text="x = 1", new_lineno=1)],
            )
        ],
    )
    vcs.get_diff.return_value = sample_diff

    session = ReviewSession(vcs=vcs, config=cfg)
    session.rescan()
    assert vcs.get_diff.call_count == 1

    session.select_entry(e1)
    assert vcs.get_diff.call_count == 1

    session.set_viewed("a.py", True)
    assert len(session.visible_unstaged()) == 2
    assert session.select_adjacent_unstaged(1) == e2
    assert session.select_adjacent_unstaged(-1) == e1
    session.set_hide_viewed(True)
    assert [e.path for e in session.visible_unstaged()] == ["b.py"]


def test_cli_frontend_selection(tmp_path: Path) -> None:
    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()
    parser = build_arg_parser()

    args_tui = parser.parse_args(["--tui"])
    assert select_frontend(args_tui, cfg) == "tui"

    args_gui = parser.parse_args(["--gui"])
    assert select_frontend(args_gui, cfg) == "gui"


def test_tui_widgets_instantiate_and_render(tmp_path: Path) -> None:
    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()
    vcs = MagicMock(spec=VCSBackend)
    vcs.repo_root = tmp_path
    vcs.name = "git"
    vcs.supports_amend = True
    vcs.get_branch_or_info.return_value = "main"
    vcs.scan_status.return_value = ([], [])

    session = ReviewSession(vcs=vcs, config=cfg)
    app = ReviewTuiApp(session)
    assert app.session is session

    diff_pane = DiffOptionList(id="diff_list")
    sample_diff = FileDiff(
        path="a.py",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(kind="add", text="x = 1", new_lineno=1)],
            )
        ],
    )
    diff_pane.render_file_diff(
        sample_diff,
        render_mode="full",
        colors=cfg.colors,
        dark_theme=True,
    )
    assert diff_pane.styles.text_wrap == "nowrap"
    assert diff_pane.styles.text_overflow == "clip"
    assert diff_pane.option_count == 2
    assert diff_pane.copyable_text() == "@@ -1,1 +1,1 @@"

    diff_pane.highlighted = 1
    diff_pane.action_line_end()
    assert diff_pane.word_col == 5
    diff_pane.action_line_home()
    assert diff_pane.word_col == 1
    diff_pane.action_word_right()
    assert diff_pane.word_col == 5
    diff_pane.action_word_left()
    assert diff_pane.word_col == 1

    diff_pane.highlighted = 0
    diff_pane.action_cursor_up()
    assert diff_pane.highlighted == 0
    diff_pane.action_cursor_down()
    assert diff_pane.highlighted == 1
    diff_pane.action_cursor_down()
    assert diff_pane.highlighted == 1
