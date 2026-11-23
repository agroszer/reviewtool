"""Tests for external tool placeholder expansion and execution."""

from pathlib import Path

from reviewtool.models import CursorContext, ToolSpec
from reviewtool.tools import (
    build_placeholder_map,
    expand_tool_command,
    normalize_textual_shortcut,
    run_tool_output,
)


def test_all_placeholders_expanded(tmp_path: Path) -> None:
    ctx = CursorContext(
        file="src/pkg/module.py",
        abs_file=str(tmp_path / "src/pkg/module.py"),
        line=42,
        old_line=40,
        new_line=42,
        col=12,
        word="calculate_price",
        selection="calculate_price(x)",
        line_text="    return calculate_price(x)",
        status="M",
        staged=True,
    )
    pmap = build_placeholder_map(
        ctx=ctx,
        repo_root=tmp_path,
        vcs_name="git",
        branch="main",
    )

    expected_keys = {
        "file",
        "abs_file",
        "dir",
        "abs_dir",
        "filename",
        "stem",
        "ext",
        "line",
        "old_line",
        "new_line",
        "col",
        "word",
        "selection",
        "line_text",
        "repo_root",
        "vcs",
        "branch",
        "status",
        "staged",
    }
    assert set(pmap.keys()) == expected_keys
    assert pmap["file"] == "src/pkg/module.py"
    assert pmap["dir"] == "src/pkg"
    assert pmap["filename"] == "module.py"
    assert pmap["stem"] == "module"
    assert pmap["ext"] == "py"
    assert pmap["line"] == "42"
    assert pmap["old_line"] == "40"
    assert pmap["new_line"] == "42"
    assert pmap["col"] == "12"
    assert pmap["word"] == "calculate_price"
    assert pmap["selection"] == "calculate_price(x)"
    assert pmap["vcs"] == "git"
    assert pmap["branch"] == "main"
    assert pmap["status"] == "M"
    assert pmap["staged"] == "1"

    argv = expand_tool_command(
        "mytool --file {file}:{line}:{col} --word {word} --vcs {vcs}",
        pmap,
    )
    assert argv == [
        "mytool",
        "--file",
        "src/pkg/module.py:42:12",
        "--word",
        "calculate_price",
        "--vcs",
        "git",
    ]


def test_run_tool_output(tmp_path: Path) -> None:
    sub = tmp_path / "subdir"
    sub.mkdir()
    tool = ToolSpec(
        key="pwd_test",
        name="PWD Test",
        command="python3 -c 'import os, sys; print(os.getcwd(), sys.argv[1])' {word}",
        mode="output",
    )
    ctx = CursorContext(file="a.py", line=10, word="hello_world")
    pmap = build_placeholder_map(ctx, tmp_path, "git", "main")
    argv = expand_tool_command(tool.command, pmap)
    res = run_tool_output(tool, argv, tmp_path)
    assert res.returncode == 0
    assert res.stdout.strip() == f"{tmp_path.resolve()} hello_world"


def test_normalize_textual_shortcut_modifiers() -> None:
    assert normalize_textual_shortcut("e") == "e"
    assert normalize_textual_shortcut("Ctrl+E") == "ctrl+e"
    assert normalize_textual_shortcut("Alt+G") == "alt+g"
    assert normalize_textual_shortcut("Ctrl+Alt+B") == "ctrl+alt+b"
    assert normalize_textual_shortcut("Ctrl+Shift+F") == "ctrl+F"
    assert normalize_textual_shortcut("Shift+F4") == "shift+f4"
    assert normalize_textual_shortcut("F12") == "f12"
