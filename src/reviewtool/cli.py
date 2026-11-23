"""Command-line entry point for reviewtool (--gui / --tui / auto-detect)."""

import argparse
import os
import sys
from pathlib import Path

from reviewtool.config import ConfigManager
from reviewtool.state import ReviewSession
from reviewtool.ui_qt import run_qt_app
from reviewtool.ui_tui import run_tui_app
from reviewtool.vcs import VCSError, detect_vcs


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for reviewtool."""
    parser = argparse.ArgumentParser(
        prog="reviewtool",
        description=(
            "Local pre-commit code review tool for Git and SVN "
            "(PyQt6 GUI + Textual TUI)."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gui",
        action="store_true",
        help="Launch the PyQt6 desktop GUI frontend.",
    )
    mode_group.add_argument(
        "--tui",
        action="store_true",
        help="Launch the Textual terminal UI frontend.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to generic options config.ini "
            "(default: ~/.config/reviewtool/config.ini)."
        ),
    )
    parser.add_argument(
        "--tools-config",
        type=Path,
        default=None,
        help="Path to external tools.ini (default: ~/.config/reviewtool/tools.ini).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Path inside a Git or SVN repository (default: current directory).",
    )
    return parser


def has_graphical_display() -> bool:
    """Return True if a graphical display server is available."""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def select_frontend(args: argparse.Namespace, config: ConfigManager) -> str:
    """Determine whether to run 'gui' or 'tui'."""
    if args.tui:
        return "tui"
    if args.gui:
        return "gui"
    pref = config.general.default_ui.lower()
    if pref == "tui":
        return "tui"
    if pref == "gui" and has_graphical_display():
        return "gui"
    return "gui" if has_graphical_display() else "tui"


def main(argv: list[str] | None = None) -> int:
    """Run reviewtool CLI."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = ConfigManager(
        config_path=args.config,
        tools_path=args.tools_config,
    )
    config.load()

    try:
        vcs = detect_vcs(args.path)
    except VCSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    os.chdir(vcs.repo_root)
    session = ReviewSession(vcs=vcs, config=config)
    frontend = select_frontend(args, config)

    if frontend == "tui":
        return run_tui_app(session)

    return run_qt_app(session)


if __name__ == "__main__":
    raise SystemExit(main())
