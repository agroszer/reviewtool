"""Tests for split INI configuration (config.ini and tools.ini)."""

from pathlib import Path

from reviewtool.config import ConfigManager


def test_config_creates_split_files_and_persists_asap(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.ini"
    tools_file = tmp_path / "tools.ini"

    mgr = ConfigManager(config_path=cfg_file, tools_path=tools_file)
    mgr.load()

    assert cfg_file.is_file()
    assert tools_file.is_file()
    assert len(mgr.tools) >= 4
    tools_mtime_before = tools_file.read_text(encoding="utf-8")

    mgr.set_context_lines(7)
    mgr.set_render_mode("word_diff")
    mgr.set_full_file(True)
    mgr.set_hide_viewed(True)
    mgr.set_file_list_font_size(14)
    mgr.set_diff_font_family("Fira Code")
    mgr.set_diff_font_size(12)
    mgr.set_commit_font_size(11)
    mgr.set_theme("high_contrast")
    mgr.update_qt_geometry(
        x=250,
        y=140,
        width=1600,
        height=950,
        maximized=False,
        main_splitter=[420, 1180],
        left_splitter=[300, 350, 300],
    )

    mgr2 = ConfigManager(config_path=cfg_file, tools_path=tools_file)
    mgr2.load()
    assert mgr2.general.context_lines == 7
    assert mgr2.general.render_mode == "word_diff"
    assert mgr2.general.full_file is True
    assert mgr2.general.hide_viewed is True
    assert mgr2.general.file_list_font_size == 14
    assert mgr2.general.diff_font_family == "Fira Code"
    assert mgr2.general.diff_font_size == 12
    assert mgr2.general.commit_font_size == 11
    assert mgr2.general.theme == "high_contrast"
    assert mgr2.colors.bg == "#000000"
    assert mgr2.colors.add_fg == "#55ff55"
    assert mgr2.qt_geometry.window_x == 250
    assert mgr2.qt_geometry.window_y == 140
    assert mgr2.qt_geometry.window_width == 1600
    assert mgr2.qt_geometry.window_height == 950
    assert mgr2.qt_geometry.main_splitter == [420, 1180]
    assert mgr2.qt_geometry.left_splitter == [300, 350, 300]
    assert tools_file.read_text(encoding="utf-8") == tools_mtime_before
