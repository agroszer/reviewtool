"""PyQt6 interactive inline diff widget supporting 3 render modes & expandable gaps."""

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QFont,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)
from pygments.lexer import Lexer

from reviewtool.config import ColorSchemeConfig, DARK_COLOR_SCHEME
from reviewtool.diff import compute_expand_dividers, extract_word_at_column
from reviewtool.models import (
    DiffLine,
    ExpandDivider,
    FileDiff,
    RenderMode,
    ToolSpec,
)
from reviewtool.syntax import get_lexer_for_path, tokenize_line
from reviewtool.tools import split_shortcuts


@dataclass
class RenderedRow:
    """Metadata for a single visual block (line) in the Qt diff editor."""

    row_kind: str
    hunk_index: int | None = None
    line_index: int | None = None
    diff_line: DiffLine | None = None
    divider: ExpandDivider | None = None
    prefix_len: int = 0
    raw_code: str = ""


class LineNumberGutter(QWidget):
    """Non-selectable left margin widget painting old/new line numbers & +/- signs."""

    def __init__(self, editor: "DiffViewWidget") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.editor.paint_gutter(event)


class DiffViewWidget(QPlainTextEdit):
    """Custom QPlainTextEdit rendering inline unified diffs with a separate gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setCursorWidth(2)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.rows: list[RenderedRow] = []
        self.file_diff: FileDiff | None = None
        self.render_mode: RenderMode = "full"
        self.is_staged: bool = False
        self.dark_theme: bool = True
        self.colors: ColorSchemeConfig = DARK_COLOR_SCHEME
        self.tools: list[ToolSpec] = []

        self.gutter = LineNumberGutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)

        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(mono)
        self._update_gutter_width()

        self.on_cursor_changed: (
            Callable[[int, int | None, int | None, int, str, str, str], None]
            | None
        ) = None
        self.on_expand_divider: Callable[[ExpandDivider], None] | None = None
        self.on_stage_hunk: Callable[[int], None] | None = None
        self.on_unstage_hunk: Callable[[int], None] | None = None
        self.on_revert_hunk: Callable[[int], None] | None = None
        self.on_stage_lines: Callable[[int, set[int]], None] | None = None
        self.on_unstage_lines: Callable[[int, set[int]], None] | None = None
        self.on_revert_lines: Callable[[int, set[int]], None] | None = None
        self.on_run_tool: Callable[[ToolSpec], None] | None = None

        self._copy_shortcut = QShortcut(
            QKeySequence(QKeySequence.StandardKey.Copy), self
        )
        self._copy_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._copy_shortcut.activated.connect(self.copy_clean_text)
        self._tool_shortcuts: list[QShortcut] = []
        self.cursorPositionChanged.connect(self._handle_cursor_changed)

    def set_diff_font(self, family: str, point_size: int) -> None:
        """Apply configurable font family and point size to the diff view."""
        mono = QFont(family or "Monospace", max(6, point_size))
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(mono)
        self.gutter.setFont(mono)
        self._update_gutter_width()
        self.gutter.update()

    def set_font_size(self, point_size: int) -> None:
        """Apply configurable monospace font point size to the diff view."""
        self.set_diff_font(self.font().family(), point_size)

    def gutter_width(self) -> int:
        """Calculate pixel width required for the non-selectable line-number gutter."""
        char_w = self.fontMetrics().horizontalAdvance("9")
        if self.render_mode == "simple":
            return char_w * 9 + 14
        return char_w * 15 + 18

    def _update_gutter_width(self, _new_block_count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(
            QRect(cr.left(), cr.top(), self.gutter_width(), cr.height())
        )

    def paint_gutter(self, event: QPaintEvent) -> None:
        """Paint old/new line numbers and +/- symbols into `self.gutter`."""
        painter = QPainter(self.gutter)
        painter.setFont(self.font())
        gutter_bg = QColor(self.colors.bg).darker(
            108 if self.colors.is_dark else 104
        )
        painter.fillRect(event.rect(), gutter_bg)

        g_width = self.gutter.width()
        border_col = QColor(self.colors.gutter_fg)
        border_col.setAlpha(90)
        painter.setPen(border_col)
        painter.drawLine(
            g_width - 1, event.rect().top(), g_width - 1, event.rect().bottom()
        )

        cur_block_num = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(
            self.blockBoundingGeometry(block)
            .translated(self.contentOffset())
            .top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        line_h = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if 0 <= block_num < len(self.rows):
                    self._paint_gutter_row(
                        painter,
                        self.rows[block_num],
                        top,
                        bottom - top,
                        line_h,
                        g_width,
                        is_active=(block_num == cur_block_num),
                    )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

    def _paint_gutter_row(
        self,
        painter: QPainter,
        row: RenderedRow,
        top: int,
        height: int,
        line_h: int,
        g_width: int,
        is_active: bool,
    ) -> None:
        if row.row_kind == "divider":
            painter.fillRect(
                0, top, g_width - 1, height, QColor(self.colors.divider_bg)
            )
            painter.setPen(QColor(self.colors.divider_fg))
            painter.drawText(
                0,
                top,
                g_width - 6,
                line_h,
                Qt.AlignmentFlag.AlignCenter,
                "↕",
            )
            return

        if row.row_kind == "hunk_header":
            painter.fillRect(
                0, top, g_width - 1, height, QColor(self.colors.hunk_bg)
            )
            painter.setPen(QColor(self.colors.hunk_fg))
            painter.drawText(
                0,
                top,
                g_width - 6,
                line_h,
                Qt.AlignmentFlag.AlignCenter,
                "@@",
            )
            return

        dline = row.diff_line
        if dline is None:
            return

        row_bg = self._line_bg_color(dline.kind)
        if is_active:
            if row_bg is not None:
                row_bg = row_bg.lighter(135 if self.colors.is_dark else 94)
            else:
                row_bg = QColor("#2a2d2e" if self.colors.is_dark else "#e8f2ff")
        if row_bg is not None:
            painter.fillRect(0, top, g_width - 1, height, row_bg)

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
            text = f"{ln_str} {sign}"
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
            text = f"{old_s} │{new_s} {sign}"

        fg_col = QColor(self.colors.gutter_fg)
        if is_active:
            fg_col = QColor(self.colors.fg)
        painter.setPen(fg_col)
        painter.drawText(
            4,
            top,
            g_width - 10,
            line_h,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

    def copy_clean_text(self) -> None:
        """Copy selected diff text (or current line if nothing is selected)."""
        cur = self.textCursor()
        if cur.hasSelection():
            self.copy()
            return
        block_num = cur.blockNumber()
        if 0 <= block_num < len(self.rows):
            QApplication.clipboard().setText(self.rows[block_num].raw_code)

    def bind_tools(self, tools: list[ToolSpec]) -> None:
        """Bind configurable keyboard shortcuts for tools inside the diff view."""
        self.tools = list(tools)
        for sc in self._tool_shortcuts:
            sc.setEnabled(False)
            sc.setKey(QKeySequence())
            sc.deleteLater()
        self._tool_shortcuts.clear()

        for tool in self.tools:
            for single_sc in split_shortcuts(tool.shortcut):
                sc = QShortcut(QKeySequence(single_sc), self)
                sc.setContext(Qt.ShortcutContext.WindowShortcut)
                sc.activated.connect(self._make_tool_callback(tool, single_sc))
                self._tool_shortcuts.append(sc)

    def _make_tool_callback(
        self, tool: ToolSpec, key_str: str | None = None
    ) -> Callable[[], None]:
        def _trigger() -> None:
            if key_str is not None:
                fw = QApplication.focusWidget()
                if (
                    isinstance(fw, QPlainTextEdit)
                    and fw is not self
                    and not fw.isReadOnly()
                    and "+" not in key_str
                ):
                    return
            if self.on_run_tool is not None:
                self.on_run_tool(tool)

        return _trigger

    def render_diff(
        self,
        file_diff: FileDiff | None,
        render_mode: RenderMode,
        is_staged: bool,
        dark_theme: bool = True,
        colors: ColorSchemeConfig | None = None,
    ) -> None:
        """Render `file_diff` using `render_mode` ('full', 'word_diff', 'simple')."""
        v_scroll = self.verticalScrollBar().value()
        self.file_diff = file_diff
        self.render_mode = render_mode
        self.is_staged = is_staged
        self.dark_theme = dark_theme
        if colors is not None:
            self.colors = colors
        self.rows.clear()
        self.clear()

        bg_hex = self.colors.bg
        fg_hex = self.colors.fg
        self.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {bg_hex}; color: {fg_hex}; }}"
        )

        if file_diff is None:
            self._append_info_row("No file selected.")
            return
        if file_diff.is_binary:
            self._append_info_row(f"Binary file: {file_diff.path}")
            return
        if not file_diff.hunks:
            self._append_info_row(f"No diff hunks for {file_diff.path}.")
            return

        lexer = get_lexer_for_path(file_diff.path)
        dividers_by_hunk = {
            d.before_hunk_index: d for d in compute_expand_dividers(file_diff)
        }

        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        first_block = True

        for h_idx, hunk in enumerate(file_diff.hunks):
            if h_idx in dividers_by_hunk:
                first_block = self._insert_divider_row(
                    cursor, dividers_by_hunk[h_idx], first_block
                )
            first_block = self._insert_hunk_header_row(
                cursor, h_idx, hunk.header, first_block
            )
            for l_idx, dline in enumerate(hunk.lines):
                first_block = self._insert_diff_line_row(
                    cursor, h_idx, l_idx, dline, lexer, first_block
                )

        trailing_idx = len(file_diff.hunks)
        if trailing_idx in dividers_by_hunk:
            self._insert_divider_row(
                cursor, dividers_by_hunk[trailing_idx], first_block
            )

        cursor.endEditBlock()
        self._update_gutter_width()
        self.gutter.update()
        self.verticalScrollBar().setValue(v_scroll)
        self._handle_cursor_changed()

    def _append_info_row(self, message: str) -> None:
        self.rows.append(RenderedRow(row_kind="info", raw_code=message))
        self.setPlainText(message)

    def _insert_divider_row(
        self,
        cursor: QTextCursor,
        divider: ExpandDivider,
        first_block: bool,
    ) -> bool:
        if not first_block:
            cursor.insertBlock()
        label = (
            f"  [ ↕ Click or Enter to expand {divider.hidden_count} hidden lines "
            f"({divider.new_start}..{divider.new_end}) ]  "
        )
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.divider_fg))
        fmt.setBackground(QColor(self.colors.divider_bg))
        fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(label, fmt)
        self.rows.append(
            RenderedRow(
                row_kind="divider",
                divider=divider,
                prefix_len=0,
                raw_code=label,
            )
        )
        return False

    def _insert_hunk_header_row(
        self,
        cursor: QTextCursor,
        hunk_index: int,
        header_text: str,
        first_block: bool,
    ) -> bool:
        if not first_block:
            cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.hunk_fg))
        fmt.setBackground(QColor(self.colors.hunk_bg))
        cursor.insertText(header_text, fmt)
        self.rows.append(
            RenderedRow(
                row_kind="hunk_header",
                hunk_index=hunk_index,
                prefix_len=0,
                raw_code=header_text,
            )
        )
        return False

    def _insert_diff_line_row(
        self,
        cursor: QTextCursor,
        hunk_index: int,
        line_index: int,
        dline: DiffLine,
        lexer: object,
        first_block: bool,
    ) -> bool:
        if not first_block:
            cursor.insertBlock()

        line_bg = self._line_bg_color(dline.kind)
        word_bg = self._word_bg_color(dline.kind)
        base_fg = self._line_fg_color(dline.kind)

        code = dline.text
        if not code:
            if line_bg is not None:
                pad_fmt = QTextCharFormat()
                pad_fmt.setBackground(line_bg)
                cursor.insertText(" ", pad_fmt)
        else:
            self._insert_styled_code(
                cursor,
                code,
                dline,
                lexer,
                line_bg,
                word_bg,
                base_fg,
            )

        self.rows.append(
            RenderedRow(
                row_kind="line",
                hunk_index=hunk_index,
                line_index=line_index,
                diff_line=dline,
                prefix_len=0,
                raw_code=code,
            )
        )
        return False

    def _insert_styled_code(
        self,
        cursor: QTextCursor,
        code: str,
        dline: DiffLine,
        lexer: object,
        line_bg: QColor | None,
        word_bg: QColor | None,
        base_fg: QColor,
    ) -> None:
        n_chars = len(code)
        char_fg: list[QColor] = [base_fg] * n_chars
        char_bg: list[QColor | None] = [line_bg] * n_chars
        char_bold: list[bool] = [False] * n_chars

        if self.render_mode == "full" and dline.kind != "meta":
            if isinstance(lexer, Lexer):
                for s_idx, e_idx, hex_color in tokenize_line(
                    code, lexer, dark=self.dark_theme
                ):
                    if hex_color is not None:
                        qcol = QColor(hex_color)
                        for pos in range(max(0, s_idx), min(n_chars, e_idx)):
                            char_fg[pos] = qcol

        if self.render_mode in ("full", "word_diff") and word_bg is not None:
            for s_idx, e_idx in dline.word_spans:
                for pos in range(max(0, s_idx), min(n_chars, e_idx)):
                    char_bg[pos] = word_bg
                    char_bold[pos] = True

        run_start = 0
        while run_start < n_chars:
            fg = char_fg[run_start]
            bg = char_bg[run_start]
            bold = char_bold[run_start]
            run_end = run_start + 1
            while (
                run_end < n_chars
                and char_fg[run_end] == fg
                and char_bg[run_end] == bg
                and char_bold[run_end] == bold
            ):
                run_end += 1
            fmt = QTextCharFormat()
            fmt.setForeground(fg)
            if bg is not None:
                fmt.setBackground(bg)
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            cursor.insertText(code[run_start:run_end], fmt)
            run_start = run_end

    def _line_bg_color(self, kind: str) -> QColor | None:
        if kind == "add":
            return QColor(self.colors.add_bg)
        if kind == "del":
            return QColor(self.colors.del_bg)
        return None

    def _word_bg_color(self, kind: str) -> QColor | None:
        if kind == "add":
            return QColor(self.colors.add_word_bg)
        if kind == "del":
            return QColor(self.colors.del_word_bg)
        return None

    def _line_fg_color(self, kind: str) -> QColor:
        if self.render_mode == "simple":
            if kind == "add":
                return QColor(self.colors.add_fg)
            if kind == "del":
                return QColor(self.colors.del_fg)
        return QColor(self.colors.fg)

    def _handle_cursor_changed(self) -> None:
        self._highlight_current_line()
        cur = self.textCursor()
        block_num = cur.blockNumber()
        if block_num < 0 or block_num >= len(self.rows):
            return
        row = self.rows[block_num]
        col_in_block = cur.positionInBlock()
        code_col_1based = max(1, col_in_block - row.prefix_len + 1)

        old_ln: int | None = None
        new_ln: int | None = None
        target_ln = 1
        if row.diff_line is not None:
            old_ln = row.diff_line.old_lineno
            new_ln = row.diff_line.new_lineno
            target_ln = new_ln or old_ln or 1
        elif row.hunk_index is not None and self.file_diff is not None:
            if 0 <= row.hunk_index < len(self.file_diff.hunks):
                target_ln = self.file_diff.hunks[row.hunk_index].new_start or 1

        word = extract_word_at_column(row.raw_code, code_col_1based)
        sel_text = cur.selectedText().replace("\u2029", "\n").strip()

        if self.on_cursor_changed is not None:
            self.on_cursor_changed(
                target_ln,
                old_ln,
                new_ln,
                code_col_1based,
                word,
                sel_text,
                row.raw_code,
            )

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        cur_bg = QColor(
            "#2a2d2e" if self.colors.is_dark else "#e8f2ff"
        )
        cur = self.textCursor()
        block_num = cur.blockNumber()
        if 0 <= block_num < len(self.rows):
            dline = self.rows[block_num].diff_line
            if dline is not None and dline.kind == "add":
                cur_bg = QColor(self.colors.add_bg).lighter(
                    135 if self.colors.is_dark else 94
                )
            elif dline is not None and dline.kind == "del":
                cur_bg = QColor(self.colors.del_bg).lighter(
                    135 if self.colors.is_dark else 94
                )
        selection.format.setBackground(cur_bg)
        selection.format.setProperty(
            QTextCharFormat.Property.FullWidthSelection, True
        )
        selection.cursor = cur
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self.gutter.update()

    def current_hunk_and_selected_lines(
        self,
    ) -> tuple[int | None, set[int]]:
        """Return `(hunk_index, selected_line_indices)` for active cursor/selection."""
        cur = self.textCursor()
        if not self.rows:
            return None, set()

        start_pos = cur.selectionStart()
        end_pos = cur.selectionEnd()
        start_block = self.document().findBlock(start_pos).blockNumber()
        end_block = self.document().findBlock(end_pos).blockNumber()
        if start_block > end_block:
            start_block, end_block = end_block, start_block

        hunk_index: int | None = None
        selected_lines: set[int] = set()
        for b_num in range(max(0, start_block), min(len(self.rows), end_block + 1)):
            row = self.rows[b_num]
            if row.hunk_index is not None and hunk_index is None:
                hunk_index = row.hunk_index
            if (
                row.row_kind == "line"
                and row.hunk_index == hunk_index
                and row.line_index is not None
                and row.diff_line is not None
                and row.diff_line.kind in ("add", "del")
            ):
                selected_lines.add(row.line_index)

        if hunk_index is None:
            cur_block = cur.blockNumber()
            if 0 <= cur_block < len(self.rows):
                hunk_index = self.rows[cur_block].hunk_index
        return hunk_index, selected_lines

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.textCursor().hasSelection()
        ):
            block_num = self.textCursor().blockNumber()
            if 0 <= block_num < len(self.rows):
                row = self.rows[block_num]
                if (
                    row.row_kind == "divider"
                    and row.divider is not None
                    and self.on_expand_divider is not None
                ):
                    self.on_expand_divider(row.divider)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_clean_text()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            block_num = self.textCursor().blockNumber()
            if 0 <= block_num < len(self.rows):
                row = self.rows[block_num]
                if (
                    row.row_kind == "divider"
                    and row.divider is not None
                    and self.on_expand_divider is not None
                ):
                    self.on_expand_divider(row.divider)
                    return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: object) -> None:
        if not isinstance(event, QContextMenuEvent):
            return

        click_cursor = self.cursorForPosition(event.pos())
        if not self.textCursor().hasSelection():
            self.setTextCursor(click_cursor)
        self._handle_cursor_changed()

        menu = QMenu(self)
        hunk_idx, sel_lines = self.current_hunk_and_selected_lines()

        if hunk_idx is not None:
            if not self.is_staged:
                act_sh = QAction("Stage Hunk", menu)
                act_sh.triggered.connect(
                    lambda: self.on_stage_hunk(hunk_idx)
                    if self.on_stage_hunk
                    else None
                )
                menu.addAction(act_sh)

                if sel_lines:
                    act_sl = QAction(
                        f"Stage Selected Lines ({len(sel_lines)})", menu
                    )
                    act_sl.triggered.connect(
                        lambda: self.on_stage_lines(hunk_idx, sel_lines)
                        if self.on_stage_lines
                        else None
                    )
                    menu.addAction(act_sl)
            else:
                act_uh = QAction("Unstage Hunk", menu)
                act_uh.triggered.connect(
                    lambda: self.on_unstage_hunk(hunk_idx)
                    if self.on_unstage_hunk
                    else None
                )
                menu.addAction(act_uh)

                if sel_lines:
                    act_ul = QAction(
                        f"Unstage Selected Lines ({len(sel_lines)})", menu
                    )
                    act_ul.triggered.connect(
                        lambda: self.on_unstage_lines(hunk_idx, sel_lines)
                        if self.on_unstage_lines
                        else None
                    )
                    menu.addAction(act_ul)

            act_rh = QAction("Revert Hunk", menu)
            act_rh.triggered.connect(
                lambda: self.on_revert_hunk(hunk_idx)
                if self.on_revert_hunk
                else None
            )
            menu.addAction(act_rh)

            if sel_lines:
                act_rl = QAction(
                    f"Revert Selected Lines ({len(sel_lines)})", menu
                )
                act_rl.triggered.connect(
                    lambda: self.on_revert_lines(hunk_idx, sel_lines)
                    if self.on_revert_lines
                    else None
                )
                menu.addAction(act_rl)

            menu.addSeparator()

        for tool in self.tools:
            shortcut_hint = f" [{tool.shortcut}]" if tool.shortcut else ""
            act_tool = QAction(f"{tool.name}{shortcut_hint}", menu)
            act_tool.triggered.connect(self._make_tool_callback(tool))
            menu.addAction(act_tool)

        menu.addSeparator()
        act_copy = QAction("Copy (Ctrl+C)", menu)
        act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        act_copy.triggered.connect(self.copy_clean_text)
        menu.addAction(act_copy)

        menu.exec(self.mapToGlobal(QPoint(event.pos())))
