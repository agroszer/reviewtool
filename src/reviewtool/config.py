"""Split INI configuration management (config.ini and tools.ini)."""

import configparser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from reviewtool.models import RenderMode, ToolMode, ToolSpec

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "reviewtool"

DEFAULT_TOOLS_INI = """\
[tool:editor]
name = Open in Editor
command = ${EDITOR:-vim} +{line} {abs_file}
mode = terminal
shortcut = Ctrl+e
refresh_after = true

[tool:vscode]
name = VS Code at Line
command = code --goto {abs_file}:{line}:{col}
mode = background
shortcut = Ctrl+v
refresh_after = false

[tool:grep_word]
name = Grep Word in Repo
command = git grep -n {word}
mode = output
shortcut = Ctrl+g
refresh_after = false

[tool:blame]
name = Blame Line
command = {vcs} blame -L {line},{line} {file}
mode = output
shortcut = Ctrl+b
refresh_after = false
"""


def _parse_int_list(raw: str) -> list[int]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result: list[int] = []
    for part in parts:
        if part.lstrip("-").isdigit():
            result.append(int(part))
    return result


def _format_int_list(values: list[int]) -> str:
    return ",".join(str(v) for v in values)


@dataclass
class QtGeometryConfig:
    """Saved window & splitter layout for PyQt6 frontend."""

    window_x: int | None = None
    window_y: int | None = None
    window_width: int = 1400
    window_height: int = 900
    window_maximized: bool = False
    main_splitter: list[int] = field(default_factory=lambda: [380, 1020])
    left_splitter: list[int] = field(default_factory=lambda: [320, 320, 260])


@dataclass
class ColorSchemeConfig:
    """Configurable diff and syntax color scheme stored in `[colors]` of config.ini."""

    bg: str = "#1e1e1e"
    fg: str = "#d4d4d4"
    gutter_fg: str = "#858585"
    hunk_fg: str = "#569cd6"
    hunk_bg: str = "#252526"
    divider_fg: str = "#3794ff"
    divider_bg: str = "#2d2d30"
    add_bg: str = "#1e3a29"
    add_word_bg: str = "#2ea043"
    add_fg: str = "#89d185"
    del_bg: str = "#4b1818"
    del_word_bg: str = "#a31515"
    del_fg: str = "#f48771"
    is_dark: bool = True


VSCODE_DARK_SCHEME = ColorSchemeConfig()

VSCODE_LIGHT_SCHEME = ColorSchemeConfig(
    bg="#ffffff",
    fg="#000000",
    gutter_fg="#237893",
    hunk_fg="#0000ff",
    hunk_bg="#f3f3f3",
    divider_fg="#0066bf",
    divider_bg="#e8f2ff",
    add_bg="#e6ffec",
    add_word_bg="#abf2bc",
    add_fg="#116329",
    del_bg="#ffebe9",
    del_word_bg="#ff8182",
    del_fg="#a31515",
    is_dark=False,
)

GITHUB_DARK_SCHEME = ColorSchemeConfig(
    bg="#0d1117",
    fg="#e6edf3",
    gutter_fg="#6e7681",
    hunk_fg="#79c0ff",
    hunk_bg="#161b22",
    divider_fg="#58a6ff",
    divider_bg="#1c2128",
    add_bg="#122d1f",
    add_word_bg="#238636",
    add_fg="#3fb950",
    del_bg="#3c1618",
    del_word_bg="#b62324",
    del_fg="#f85149",
    is_dark=True,
)

GITHUB_LIGHT_SCHEME = ColorSchemeConfig(
    bg="#ffffff",
    fg="#1f2328",
    gutter_fg="#636c76",
    hunk_fg="#0969da",
    hunk_bg="#f6f8fa",
    divider_fg="#0969da",
    divider_bg="#ddf4ff",
    add_bg="#dafbe1",
    add_word_bg="#aceebb",
    add_fg="#1a7f37",
    del_bg="#ffebe9",
    del_word_bg="#ffcecb",
    del_fg="#cf222e",
    is_dark=False,
)

MONOKAI_SCHEME = ColorSchemeConfig(
    bg="#272822",
    fg="#f8f8f2",
    gutter_fg="#90908a",
    hunk_fg="#66d9ef",
    hunk_bg="#1e1f1c",
    divider_fg="#a6e22e",
    divider_bg="#3e3d32",
    add_bg="#233b25",
    add_word_bg="#3b6e3f",
    add_fg="#a6e22e",
    del_bg="#4a1f28",
    del_word_bg="#872638",
    del_fg="#f92672",
    is_dark=True,
)

DRACULA_SCHEME = ColorSchemeConfig(
    bg="#282a36",
    fg="#f8f8f2",
    gutter_fg="#6272a4",
    hunk_fg="#8be9fd",
    hunk_bg="#21222c",
    divider_fg="#bd93f9",
    divider_bg="#343746",
    add_bg="#1d3c2b",
    add_word_bg="#2e6b48",
    add_fg="#50fa7b",
    del_bg="#47212a",
    del_word_bg="#7d2c3b",
    del_fg="#ff5555",
    is_dark=True,
)

SOLARIZED_DARK_SCHEME = ColorSchemeConfig(
    bg="#002b36",
    fg="#839496",
    gutter_fg="#586e75",
    hunk_fg="#268bd2",
    hunk_bg="#073642",
    divider_fg="#2aa198",
    divider_bg="#094352",
    add_bg="#0b3b2c",
    add_word_bg="#1e6143",
    add_fg="#859900",
    del_bg="#3d1c22",
    del_word_bg="#6e2932",
    del_fg="#dc322f",
    is_dark=True,
)

NORD_SCHEME = ColorSchemeConfig(
    bg="#2e3440",
    fg="#d8dee9",
    gutter_fg="#616e88",
    hunk_fg="#88c0d0",
    hunk_bg="#3b4252",
    divider_fg="#81a1c1",
    divider_bg="#434c5e",
    add_bg="#293b36",
    add_word_bg="#406153",
    add_fg="#a3be8c",
    del_bg="#442e35",
    del_word_bg="#72434c",
    del_fg="#bf616a",
    is_dark=True,
)

HIGH_CONTRAST_COLOR_SCHEME = ColorSchemeConfig(
    bg="#000000",
    fg="#ffffff",
    gutter_fg="#888888",
    hunk_fg="#00ffff",
    hunk_bg="#111111",
    divider_fg="#ffff00",
    divider_bg="#222222",
    add_bg="#003300",
    add_word_bg="#006600",
    add_fg="#55ff55",
    del_bg="#330000",
    del_word_bg="#770000",
    del_fg="#ff5555",
    is_dark=True,
)

DARK_COLOR_SCHEME = VSCODE_DARK_SCHEME
LIGHT_COLOR_SCHEME = VSCODE_LIGHT_SCHEME

COLOR_SCHEME_PRESETS: tuple[tuple[str, str, ColorSchemeConfig], ...] = (
    ("vscode_dark", "VS Code Dark+", VSCODE_DARK_SCHEME),
    ("vscode_light", "VS Code Light+", VSCODE_LIGHT_SCHEME),
    ("github_dark", "GitHub Dark", GITHUB_DARK_SCHEME),
    ("github_light", "GitHub Light", GITHUB_LIGHT_SCHEME),
    ("monokai", "Monokai", MONOKAI_SCHEME),
    ("dracula", "Dracula", DRACULA_SCHEME),
    ("solarized_dark", "Solarized Dark", SOLARIZED_DARK_SCHEME),
    ("nord", "Nord", NORD_SCHEME),
    ("high_contrast", "VS Code High Contrast", HIGH_CONTRAST_COLOR_SCHEME),
)

HOTKEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("rescan", "Rescan"),
    ("context_minus", "Context -"),
    ("context_plus", "Context +"),
    ("full_file", "Full File"),
    ("hide_viewed", "Hide Viewed"),
    ("tools", "External Tools"),
    ("options", "Options"),
    ("stage_file", "Stage"),
    ("stage_all", "Stage All"),
    ("unstage_file", "Unstage"),
    ("unstage_all", "Unstage All"),
    ("stage_hunk", "Stage/Unstage Hunk"),
    ("stage_lines", "Stage/Unstage Lines"),
    ("revert_hunk", "Revert Hunk"),
    ("amend", "Amend Last Commit"),
    ("signoff", "Sign Off"),
    ("commit", "Commit"),
    ("push", "Push"),
)


def get_preset_color_scheme(name: str) -> ColorSchemeConfig:
    """Return a copy of the named preset ColorSchemeConfig."""
    key = name.strip().lower()
    alias_map: dict[str, ColorSchemeConfig] = {
        "dark": VSCODE_DARK_SCHEME,
        "light": VSCODE_LIGHT_SCHEME,
        "contrast": HIGH_CONTRAST_COLOR_SCHEME,
    }
    for preset_id, _label, scheme in COLOR_SCHEME_PRESETS:
        alias_map[preset_id] = scheme
    base = alias_map.get(key, VSCODE_DARK_SCHEME)
    return ColorSchemeConfig(
        bg=base.bg,
        fg=base.fg,
        gutter_fg=base.gutter_fg,
        hunk_fg=base.hunk_fg,
        hunk_bg=base.hunk_bg,
        divider_fg=base.divider_fg,
        divider_bg=base.divider_bg,
        add_bg=base.add_bg,
        add_word_bg=base.add_word_bg,
        add_fg=base.add_fg,
        del_bg=base.del_bg,
        del_word_bg=base.del_word_bg,
        del_fg=base.del_fg,
        is_dark=base.is_dark,
    )


@dataclass
class HotkeyConfig:
    """Configurable keyboard shortcuts for buttons on the main form."""

    rescan: str = "F5"
    context_minus: str = "["
    context_plus: str = "]"
    full_file: str = "f"
    hide_viewed: str = "h"
    tools: str = "t"
    options: str = "o"
    stage_file: str = "s"
    stage_all: str = "Ctrl+Shift+S"
    unstage_file: str = "u"
    unstage_all: str = "Ctrl+Shift+U"
    stage_hunk: str = "k"
    stage_lines: str = "l"
    revert_hunk: str = "r"
    amend: str = "Ctrl+M"
    signoff: str = "Ctrl+Shift+O"
    commit: str = "Ctrl+Return"
    push: str = "Ctrl+P"


@dataclass
class GeneralConfig:
    """Generic user options stored in config.ini and saved ASAP on UI change."""

    default_ui: str = "auto"
    context_lines: int = 3
    render_mode: RenderMode = "full"
    full_file: bool = False
    hide_viewed: bool = False
    tab_width: int = 4
    theme: str = "vscode_dark"
    file_list_font_size: int = 11
    diff_font_family: str = "Monospace"
    diff_font_size: int = 10
    commit_font_size: int = 10
    tui_left_width: int = 44


class ConfigManager:
    """Manages reading and immediate writing of config.ini and tools.ini."""

    def __init__(
        self,
        config_path: Path | None = None,
        tools_path: Path | None = None,
    ) -> None:
        base_dir = DEFAULT_CONFIG_DIR
        self.config_path = config_path or (base_dir / "config.ini")
        self.tools_path = tools_path or (base_dir / "tools.ini")
        self.general = GeneralConfig()
        self.qt_geometry = QtGeometryConfig()
        self.colors = ColorSchemeConfig()
        self.hotkeys = HotkeyConfig()
        self.tools: list[ToolSpec] = []

    def load(self) -> None:
        """Load both config.ini and tools.ini, creating defaults if missing."""
        self.load_general()
        self.load_tools()

    def load_general(self) -> None:
        """Load generic options and Qt geometry from config.ini."""
        if not self.config_path.exists():
            self.save_general()
            return

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.config_path, encoding="utf-8")

        if parser.has_section("general"):
            sec = parser["general"]
            self.general.default_ui = sec.get("default_ui", "auto").strip()
            self.general.context_lines = max(
                0, sec.getint("context_lines", fallback=3)
            )
            raw_mode = sec.get("render_mode", "full").strip()
            if raw_mode in ("full", "word_diff", "simple"):
                self.general.render_mode = cast(RenderMode, raw_mode)
            self.general.full_file = sec.getboolean("full_file", fallback=False)
            self.general.hide_viewed = sec.getboolean("hide_viewed", fallback=False)
            self.general.tab_width = max(1, sec.getint("tab_width", fallback=4))
            self.general.theme = sec.get("theme", "dark").strip()
            self.general.file_list_font_size = max(
                6, sec.getint("file_list_font_size", fallback=11)
            )
            self.general.diff_font_family = (
                sec.get("diff_font_family", "Monospace").strip() or "Monospace"
            )
            self.general.diff_font_size = max(
                6, sec.getint("diff_font_size", fallback=10)
            )
            self.general.commit_font_size = max(
                6, sec.getint("commit_font_size", fallback=10)
            )
            self.general.tui_left_width = max(
                20, min(140, sec.getint("tui_left_width", fallback=44))
            )
            self.colors = get_preset_color_scheme(self.general.theme)

        if parser.has_section("colors"):
            csec = parser["colors"]
            self.colors.bg = csec.get("bg", self.colors.bg).strip()
            self.colors.fg = csec.get("fg", self.colors.fg).strip()
            self.colors.gutter_fg = csec.get(
                "gutter_fg", self.colors.gutter_fg
            ).strip()
            self.colors.hunk_fg = csec.get("hunk_fg", self.colors.hunk_fg).strip()
            self.colors.hunk_bg = csec.get("hunk_bg", self.colors.hunk_bg).strip()
            self.colors.divider_fg = csec.get(
                "divider_fg", self.colors.divider_fg
            ).strip()
            self.colors.divider_bg = csec.get(
                "divider_bg", self.colors.divider_bg
            ).strip()
            self.colors.add_bg = csec.get("add_bg", self.colors.add_bg).strip()
            self.colors.add_word_bg = csec.get(
                "add_word_bg", self.colors.add_word_bg
            ).strip()
            self.colors.add_fg = csec.get("add_fg", self.colors.add_fg).strip()
            self.colors.del_bg = csec.get("del_bg", self.colors.del_bg).strip()
            self.colors.del_word_bg = csec.get(
                "del_word_bg", self.colors.del_word_bg
            ).strip()
            self.colors.del_fg = csec.get("del_fg", self.colors.del_fg).strip()

        if parser.has_section("ui_qt"):
            qsec = parser["ui_qt"]
            raw_x = qsec.get("window_x", "").strip()
            raw_y = qsec.get("window_y", "").strip()
            self.qt_geometry.window_x = (
                int(raw_x) if raw_x.lstrip("-").isdigit() else None
            )
            self.qt_geometry.window_y = (
                int(raw_y) if raw_y.lstrip("-").isdigit() else None
            )
            self.qt_geometry.window_width = max(
                400, qsec.getint("window_width", fallback=1400)
            )
            self.qt_geometry.window_height = max(
                300, qsec.getint("window_height", fallback=900)
            )
            self.qt_geometry.window_maximized = qsec.getboolean(
                "window_maximized", fallback=False
            )
            main_sp = _parse_int_list(qsec.get("main_splitter", ""))
            if len(main_sp) == 2:
                self.qt_geometry.main_splitter = main_sp
            left_sp = _parse_int_list(qsec.get("left_splitter", ""))
            if len(left_sp) == 3:
                self.qt_geometry.left_splitter = left_sp

        if parser.has_section("hotkeys"):
            hsec = parser["hotkeys"]
            for fld in asdict(self.hotkeys):
                if fld in hsec:
                    setattr(self.hotkeys, fld, hsec.get(fld, "").strip())

    def save_general(self) -> None:
        """Persist generic options and Qt geometry to config.ini immediately."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser(interpolation=None)

        parser["general"] = {
            "default_ui": self.general.default_ui,
            "context_lines": str(self.general.context_lines),
            "render_mode": self.general.render_mode,
            "full_file": "true" if self.general.full_file else "false",
            "hide_viewed": "true" if self.general.hide_viewed else "false",
            "tab_width": str(self.general.tab_width),
            "theme": self.general.theme,
            "file_list_font_size": str(self.general.file_list_font_size),
            "diff_font_family": self.general.diff_font_family,
            "diff_font_size": str(self.general.diff_font_size),
            "commit_font_size": str(self.general.commit_font_size),
            "tui_left_width": str(self.general.tui_left_width),
        }

        parser["colors"] = {
            "bg": self.colors.bg,
            "fg": self.colors.fg,
            "gutter_fg": self.colors.gutter_fg,
            "hunk_fg": self.colors.hunk_fg,
            "hunk_bg": self.colors.hunk_bg,
            "divider_fg": self.colors.divider_fg,
            "divider_bg": self.colors.divider_bg,
            "add_bg": self.colors.add_bg,
            "add_word_bg": self.colors.add_word_bg,
            "add_fg": self.colors.add_fg,
            "del_bg": self.colors.del_bg,
            "del_word_bg": self.colors.del_word_bg,
            "del_fg": self.colors.del_fg,
        }

        ui_qt_dict: dict[str, str] = {
            "window_width": str(self.qt_geometry.window_width),
            "window_height": str(self.qt_geometry.window_height),
            "window_maximized": (
                "true" if self.qt_geometry.window_maximized else "false"
            ),
            "main_splitter": _format_int_list(self.qt_geometry.main_splitter),
            "left_splitter": _format_int_list(self.qt_geometry.left_splitter),
        }
        if self.qt_geometry.window_x is not None:
            ui_qt_dict["window_x"] = str(self.qt_geometry.window_x)
        if self.qt_geometry.window_y is not None:
            ui_qt_dict["window_y"] = str(self.qt_geometry.window_y)
        parser["ui_qt"] = ui_qt_dict

        parser["hotkeys"] = asdict(self.hotkeys)

        with self.config_path.open("w", encoding="utf-8") as fh:
            parser.write(fh)

    def load_tools(self) -> None:
        """Load external tool specifications from tools.ini."""
        if not self.tools_path.exists():
            self.tools_path.parent.mkdir(parents=True, exist_ok=True)
            self.tools_path.write_text(DEFAULT_TOOLS_INI, encoding="utf-8")

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.tools_path, encoding="utf-8")

        loaded: list[ToolSpec] = []
        for section in parser.sections():
            if not section.startswith("tool:"):
                continue
            key = section.split(":", 1)[1].strip()
            sec = parser[section]
            command = sec.get("command", "").strip()
            if not command:
                continue
            name = sec.get("name", key).strip()
            raw_mode = sec.get("mode", "background").strip()
            mode: ToolMode = "background"
            if raw_mode in ("background", "terminal", "output"):
                mode = cast(ToolMode, raw_mode)
            shortcut = sec.get("shortcut", "").strip()
            refresh_after = sec.getboolean("refresh_after", fallback=False)
            loaded.append(
                ToolSpec(
                    key=key,
                    name=name,
                    command=command,
                    mode=mode,
                    shortcut=shortcut,
                    refresh_after=refresh_after,
                )
            )
        self.tools = loaded

    def set_context_lines(self, lines: int) -> None:
        """Update context_lines and save config.ini immediately."""
        self.general.context_lines = max(0, lines)
        self.save_general()

    def set_render_mode(self, mode: RenderMode) -> None:
        """Update render_mode and save config.ini immediately."""
        self.general.render_mode = mode
        self.save_general()

    def set_full_file(self, full_file: bool) -> None:
        """Update full_file toggle and save config.ini immediately."""
        self.general.full_file = full_file
        self.save_general()

    def set_hide_viewed(self, hide_viewed: bool) -> None:
        """Update hide_viewed toggle and save config.ini immediately."""
        self.general.hide_viewed = hide_viewed
        self.save_general()

    def set_theme(self, theme: str) -> None:
        """Switch color scheme preset ('dark', 'light', 'high_contrast') ASAP."""
        self.general.theme = theme.strip().lower()
        self.colors = get_preset_color_scheme(self.general.theme)
        self.save_general()

    def set_file_list_font_size(self, size: int) -> None:
        """Update file list font size and save config.ini immediately."""
        self.general.file_list_font_size = max(6, min(48, size))
        self.save_general()

    def set_diff_font_family(self, family: str) -> None:
        """Update diff view font family and save config.ini immediately."""
        clean = family.strip()
        if clean:
            self.general.diff_font_family = clean
            self.save_general()

    def set_diff_font_size(self, size: int) -> None:
        """Update diff view font size and save config.ini immediately."""
        self.general.diff_font_size = max(6, min(48, size))
        self.save_general()

    def set_commit_font_size(self, size: int) -> None:
        """Update commit message font size and save config.ini immediately."""
        self.general.commit_font_size = max(6, min(48, size))
        self.save_general()

    def set_tui_left_width(self, width: int) -> None:
        """Update TUI left pane width and save config.ini immediately."""
        self.general.tui_left_width = max(20, min(140, width))
        self.save_general()

    def update_qt_geometry(
        self,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        maximized: bool | None = None,
        main_splitter: list[int] | None = None,
        left_splitter: list[int] | None = None,
    ) -> None:
        """Update Qt window/splitter geometry and save config.ini immediately."""
        if x is not None:
            self.qt_geometry.window_x = x
        if y is not None:
            self.qt_geometry.window_y = y
        if width is not None and width > 0:
            self.qt_geometry.window_width = width
        if height is not None and height > 0:
            self.qt_geometry.window_height = height
        if maximized is not None:
            self.qt_geometry.window_maximized = maximized
        if main_splitter is not None and len(main_splitter) == 2:
            self.qt_geometry.main_splitter = list(main_splitter)
        if left_splitter is not None and len(left_splitter) == 3:
            self.qt_geometry.left_splitter = list(left_splitter)
        self.save_general()
