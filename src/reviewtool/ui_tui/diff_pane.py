"""Textual TUI interactive inline diff pane with 3 render modes & word cursor."""

from dataclasses import dataclass
import re
from typing import ClassVar

from pygments.lexer import Lexer
from rich.style import Style
from rich.text import Text
from textual.binding import Binding, BindingType
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from reviewtool.config import ColorSchemeConfig, DARK_COLOR_SCHEME
from reviewtool.diff import compute_expand_dividers, extract_word_at_column
from reviewtool.models import (
    DiffLine,
    ExpandDivider,
    FileDiff,
    RenderMode,
)
from reviewtool.syntax import get_lexer_for_path, tokenize_line

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class TuiDiffRow:
    """Metadata for a single option row inside the Textual diff OptionList."""

    row_kind: str
    hunk_index: int | None = None
    line_index: int | None = None
    diff_line: DiffLine | None = None
    divider: ExpandDivider | None = None
    raw_code: str = ""


class DiffOptionList(OptionList):
    """Scrollable interactive diff view for the Textual TUI."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("left", "col_left", "Left", show=False),
        Binding("right", "col_right", "Right", show=False),
        Binding("ctrl+left", "word_left", "Word Left", show=False),
        Binding("ctrl+right", "word_right", "Word Right", show=False),
        Binding("home", "line_home", "Line Start", show=False),
        Binding("end", "line_end", "Line End", show=False),
        Binding("ctrl+home", "first", "First Line", show=False),
        Binding("ctrl+end", "last", "Last Line", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.styles.text_wrap = "nowrap"
        self.styles.text_overflow = "clip"
        self.rows: list[TuiDiffRow] = []
        self.file_diff: FileDiff | None = None
        self.render_mode: RenderMode = "full"
        self.color_scheme: ColorSchemeConfig = DARK_COLOR_SCHEME
        self.dark_theme: bool = True
        self.selected_lines_by_hunk: dict[int, set[int]] = {}
        self.word_col: int = 1
        self._active_row_idx: int = 0
        self._lexer: Lexer = get_lexer_for_path("")

    def render_file_diff(
        self,
        file_diff: FileDiff | None,
        render_mode: RenderMode,
        colors: ColorSchemeConfig | None = None,
        dark_theme: bool = True,
        reset_cursor: bool = False,
    ) -> None:
        """Populate the OptionList with styled Rich Text lines."""
        prev_idx = 0 if reset_cursor else self.highlighted
        saved_scroll_y = 0.0 if reset_cursor else self.scroll_y
        self.clear_options()
        self.rows.clear()
        self.selected_lines_by_hunk.clear()
        self.file_diff = file_diff
        self.render_mode = render_mode
        if colors is not None:
            self.color_scheme = colors
        self.dark_theme = dark_theme
        self.styles.background = self.color_scheme.bg
        self.styles.color = self.color_scheme.fg

        if file_diff is None:
            self._add_info("No file selected.")
            return
        if file_diff.is_binary:
            self._add_info(f"Binary file: {file_diff.path}")
            return
        if not file_diff.hunks:
            self._add_info(f"No diff hunks for {file_diff.path}.")
            return

        self._lexer = get_lexer_for_path(file_diff.path)
        dividers = {
            d.before_hunk_index: d for d in compute_expand_dividers(file_diff)
        }

        self._active_row_idx = prev_idx if prev_idx is not None else 0
        options: list[Option] = []
        for h_idx, hunk in enumerate(file_diff.hunks):
            if h_idx in dividers:
                options.append(
                    self._build_divider_option(dividers[h_idx], len(self.rows))
                )
            options.append(
                self._build_hunk_header_option(
                    h_idx, hunk.header, len(self.rows)
                )
            )
            for l_idx, dline in enumerate(hunk.lines):
                options.append(
                    self._build_diff_line_option(
                        h_idx, l_idx, dline, self._lexer, len(self.rows)
                    )
                )

        trailing = len(file_diff.hunks)
        if trailing in dividers:
            options.append(
                self._build_divider_option(dividers[trailing], len(self.rows))
            )

        self.add_options(options)
        if self.option_count > 0:
            target = (
                min(prev_idx, self.option_count - 1)
                if prev_idx is not None
                else 0
            )
            self._active_row_idx = target
            self.highlighted = target
            if not reset_cursor and self.is_mounted:
                self.scroll_to(y=saved_scroll_y, animate=False)
                self.scroll_to_highlight()

    def _add_info(self, text: str) -> None:
        self.rows.append(TuiDiffRow(row_kind="info", raw_code=text))
        self.add_option(Option(Text(text, style="italic dim")))

    def _render_banner_text(
        self, text: str, fg: str, bg: str, row_index: int
    ) -> Text:
        is_active = row_index == self._active_row_idx
        mark = "▶" if is_active else " "
        full = self._pad_full_width(f"{mark}{text}")
        if not is_active:
            return Text(full, style=Style(color=fg, bgcolor=bg, bold=True))
        pos = max(1, min(len(full) - 1, self.word_col))
        rich = Text()
        base_style = Style(color=fg, bgcolor=bg, bold=True)
        cur_style = Style(color=fg, bgcolor=bg, bold=True, reverse=True)
        rich.append(full[:pos], style=base_style)
        rich.append(full[pos : pos + 1], style=cur_style)
        rich.append(full[pos + 1 :], style=base_style)
        return rich

    def _build_divider_option(
        self, divider: ExpandDivider, row_index: int = -1
    ) -> Option:
        label = (
            f" [ ↕ Press Enter to expand {divider.hidden_count} hidden lines "
            f"({divider.new_start}..{divider.new_end}) ]  "
        )
        self.rows.append(
            TuiDiffRow(row_kind="divider", divider=divider, raw_code=label)
        )
        return Option(
            self._render_banner_text(
                label,
                self.color_scheme.divider_fg,
                self.color_scheme.divider_bg,
                row_index,
            )
        )

    def _build_hunk_header_option(
        self, hunk_index: int, header_text: str, row_index: int = -1
    ) -> Option:
        self.rows.append(
            TuiDiffRow(
                row_kind="hunk_header",
                hunk_index=hunk_index,
                raw_code=header_text,
            )
        )
        return Option(
            self._render_banner_text(
                f" {header_text}",
                self.color_scheme.hunk_fg,
                self.color_scheme.hunk_bg,
                row_index,
            )
        )

    def _pad_full_width(self, text: str) -> str:
        region_w = self.scrollable_content_region.width
        width = region_w if region_w > 0 else (
            self.size.width if self.size.width > 0 else 120
        )
        if len(text) < width:
            return text + (" " * (width - len(text)))
        return text

    def _render_diff_line_text(
        self,
        hunk_index: int,
        line_index: int,
        dline: DiffLine,
        lexer: Lexer,
        row_index: int = -1,
    ) -> Text:
        is_sel = line_index in self.selected_lines_by_hunk.get(
            hunk_index, set()
        )
        is_active = row_index == self._active_row_idx
        mark = "*" if is_sel else ("▶" if is_active else " ")
        if dline.kind == "add":
            sign = "+"
        elif dline.kind == "del":
            sign = "-"
        elif dline.kind == "meta":
            sign = "\\"
        else:
            sign = " "

        if self.render_mode == "simple":
            ln = dline.new_lineno or dline.old_lineno
            ln_str = f"{ln:5d}" if ln is not None else "     "
            prefix = f"{mark}{ln_str} | {sign} "
        else:
            old_s = (
                f"{dline.old_lineno:5d}"
                if dline.old_lineno is not None
                else "     "
            )
            new_s = (
                f"{dline.new_lineno:5d}"
                if dline.new_lineno is not None
                else "     "
            )
            prefix = f"{mark}{old_s} | {new_s} | {sign} "

        line_bg = (
            self.color_scheme.add_bg
            if dline.kind == "add"
            else (
                self.color_scheme.del_bg
                if dline.kind == "del"
                else (
                    "#2a2d2e"
                    if (is_active and self.dark_theme)
                    else (
                        "#e8f2ff"
                        if is_active
                        else self.color_scheme.bg
                    )
                )
            )
        )
        word_bg = (
            self.color_scheme.add_word_bg
            if dline.kind == "add"
            else (
                self.color_scheme.del_word_bg if dline.kind == "del" else None
            )
        )
        base_fg = (
            self.color_scheme.add_fg
            if (self.render_mode == "simple" and dline.kind == "add")
            else (
                self.color_scheme.del_fg
                if (self.render_mode == "simple" and dline.kind == "del")
                else self.color_scheme.fg
            )
        )

        rich_line = Text()
        gutter_style = Style(color=self.color_scheme.gutter_fg, bgcolor=line_bg)
        rich_line.append(prefix, style=gutter_style)

        code = dline.text
        n_chars = len(code)
        cursor_pos = max(0, min(n_chars - 1, self.word_col - 1)) if is_active else -1
        if n_chars == 0:
            cell_style = (
                Style(
                    color=self.color_scheme.bg,
                    bgcolor=self.color_scheme.fg,
                    bold=True,
                )
                if is_active
                else Style(bgcolor=line_bg)
            )
            rich_line.append(" ", style=cell_style)
        else:
            char_fg: list[str] = [base_fg] * n_chars
            char_bg: list[str | None] = [line_bg] * n_chars
            char_bold: list[bool] = [False] * n_chars
            char_reverse: list[bool] = [False] * n_chars

            if self.render_mode == "full" and dline.kind != "meta":
                for s_idx, e_idx, hex_color in tokenize_line(
                    code, lexer, dark=self.dark_theme
                ):
                    if hex_color is not None:
                        for pos in range(max(0, s_idx), min(n_chars, e_idx)):
                            char_fg[pos] = hex_color

            if self.render_mode in ("full", "word_diff") and word_bg is not None:
                for s_idx, e_idx in dline.word_spans:
                    for pos in range(max(0, s_idx), min(n_chars, e_idx)):
                        char_bg[pos] = word_bg
                        char_bold[pos] = True

            if 0 <= cursor_pos < n_chars:
                char_reverse[cursor_pos] = True
                char_bold[cursor_pos] = True

            run_start = 0
            while run_start < n_chars:
                fg = char_fg[run_start]
                bg = char_bg[run_start]
                bold = char_bold[run_start]
                rev = char_reverse[run_start]
                run_end = run_start + 1
                while (
                    run_end < n_chars
                    and char_fg[run_end] == fg
                    and char_bg[run_end] == bg
                    and char_bold[run_end] == bold
                    and char_reverse[run_end] == rev
                ):
                    run_end += 1
                rich_line.append(
                    code[run_start:run_end],
                    style=Style(color=fg, bgcolor=bg, bold=bold, reverse=rev),
                )
                run_start = run_end

        region_w = self.scrollable_content_region.width
        target_width = region_w if region_w > 0 else (
            self.size.width if self.size.width > 0 else 120
        )
        cur_len = len(prefix) + n_chars
        if cur_len < target_width:
            rich_line.append(
                " " * (target_width - cur_len),
                style=Style(bgcolor=line_bg),
            )
        return rich_line

    def _build_diff_line_option(
        self,
        hunk_index: int,
        line_index: int,
        dline: DiffLine,
        lexer: Lexer,
        row_index: int = -1,
    ) -> Option:
        rich_line = self._render_diff_line_text(
            hunk_index, line_index, dline, lexer, row_index
        )
        self.rows.append(
            TuiDiffRow(
                row_kind="line",
                hunk_index=hunk_index,
                line_index=line_index,
                diff_line=dline,
                raw_code=dline.text,
            )
        )
        return Option(rich_line)

    def _refresh_row_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.rows) or idx >= self.option_count:
            return
        row = self.rows[idx]
        if (
            row.row_kind == "line"
            and row.hunk_index is not None
            and row.line_index is not None
            and row.diff_line is not None
        ):
            prompt = self._render_diff_line_text(
                row.hunk_index,
                row.line_index,
                row.diff_line,
                self._lexer,
                idx,
            )
            self.replace_option_prompt_at_index(idx, prompt)
        elif row.row_kind == "divider":
            prompt = self._render_banner_text(
                row.raw_code,
                self.color_scheme.divider_fg,
                self.color_scheme.divider_bg,
                idx,
            )
            self.replace_option_prompt_at_index(idx, prompt)
        elif row.row_kind == "hunk_header":
            prompt = self._render_banner_text(
                f" {row.raw_code}",
                self.color_scheme.hunk_fg,
                self.color_scheme.hunk_bg,
                idx,
            )
            self.replace_option_prompt_at_index(idx, prompt)

    def current_row(self) -> TuiDiffRow | None:
        idx = self.highlighted
        if idx is None or idx < 0 or idx >= len(self.rows):
            return None
        return self.rows[idx]

    def toggle_current_line_selection(self) -> bool:
        """Toggle selection of the current `+`/`-` diff line for partial staging."""
        row = self.current_row()
        if (
            row is None
            or row.row_kind != "line"
            or row.hunk_index is None
            or row.line_index is None
            or row.diff_line is None
            or row.diff_line.kind not in ("add", "del")
        ):
            return False

        sel_set = self.selected_lines_by_hunk.setdefault(row.hunk_index, set())
        if row.line_index in sel_set:
            sel_set.remove(row.line_index)
        else:
            sel_set.add(row.line_index)
        if self.highlighted is not None:
            self._refresh_row_at(self.highlighted)
        return True

    def copyable_text(self) -> str:
        """Return text of selected diff lines (or current line) for Ctrl+C copy."""
        if self.file_diff is not None and any(self.selected_lines_by_hunk.values()):
            lines: list[str] = []
            for h_idx, sel_indices in sorted(self.selected_lines_by_hunk.items()):
                if 0 <= h_idx < len(self.file_diff.hunks):
                    hunk = self.file_diff.hunks[h_idx]
                    for l_idx in sorted(sel_indices):
                        if 0 <= l_idx < len(hunk.lines):
                            lines.append(hunk.lines[l_idx].text)
            if lines:
                return "\n".join(lines)
        row = self.current_row()
        return row.raw_code if row is not None else ""

    def current_word(self) -> str:
        row = self.current_row()
        if row is None:
            return ""
        return extract_word_at_column(row.raw_code, self.word_col)

    def refresh_active_cursor(self) -> None:
        """Re-render only the old and new active rows in-place when row/col moves."""
        if self.file_diff is None or self.option_count == 0:
            return
        old_idx = self._active_row_idx
        new_idx = self.highlighted if self.highlighted is not None else 0
        self._active_row_idx = new_idx
        if old_idx != new_idx:
            self._refresh_row_at(old_idx)
        self._refresh_row_at(new_idx)

    def action_cursor_down(self) -> None:
        """Move highlight down one line without wrapping around at the bottom."""
        if self.option_count == 0:
            return
        cur = self.highlighted if self.highlighted is not None else 0
        if cur + 1 < self.option_count:
            self.highlighted = cur + 1

    def action_cursor_up(self) -> None:
        """Move highlight up one line without wrapping around at the top."""
        if self.option_count == 0:
            return
        cur = self.highlighted if self.highlighted is not None else 0
        if cur > 0:
            self.highlighted = cur - 1

    def _current_line_len(self) -> int:
        row = self.current_row()
        if row is None or not row.raw_code:
            return 1
        return len(row.raw_code)

    def action_col_left(self) -> None:
        """Move character cursor one column left."""
        self.word_col = max(1, min(self._current_line_len(), self.word_col) - 1)
        self.refresh_active_cursor()

    def action_col_right(self) -> None:
        """Move character cursor one column right."""
        self.word_col = min(self._current_line_len(), self.word_col + 1)
        self.refresh_active_cursor()

    def action_line_home(self) -> None:
        """Move character cursor to start of line (toggles first non-blank / col 1)."""
        row = self.current_row()
        if row is None or not row.raw_code:
            self.word_col = 1
        else:
            stripped = row.raw_code.lstrip()
            first_non_blank = (
                len(row.raw_code) - len(stripped) + 1 if stripped else 1
            )
            self.word_col = (
                1 if self.word_col == first_non_blank else first_non_blank
            )
        self.refresh_active_cursor()

    def action_line_end(self) -> None:
        """Move character cursor to end of current line."""
        self.word_col = self._current_line_len()
        self.refresh_active_cursor()

    def action_word_left(self) -> None:
        """Jump character cursor to start of previous word on the current line."""
        row = self.current_row()
        if row is None or not row.raw_code:
            self.word_col = 1
            self.refresh_active_cursor()
            return
        cur_0 = max(0, min(len(row.raw_code) - 1, self.word_col - 1))
        prev_starts = [
            m.start() + 1
            for m in _WORD_RE.finditer(row.raw_code)
            if m.start() < cur_0
        ]
        self.word_col = prev_starts[-1] if prev_starts else 1
        self.refresh_active_cursor()

    def action_word_right(self) -> None:
        """Jump character cursor to start of next word on the current line."""
        row = self.current_row()
        if row is None or not row.raw_code:
            self.word_col = 1
            self.refresh_active_cursor()
            return
        cur_0 = max(0, self.word_col - 1)
        next_starts = [
            m.start() + 1
            for m in _WORD_RE.finditer(row.raw_code)
            if m.start() > cur_0
        ]
        self.word_col = (
            next_starts[0] if next_starts else len(row.raw_code)
        )
        self.refresh_active_cursor()
