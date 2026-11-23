"""Textual TUI frontend application for reviewtool."""

from typing import cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key, MouseDown, MouseMove, MouseUp
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    OptionList,
    RichLog,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from reviewtool.config import COLOR_SCHEME_PRESETS
from reviewtool.models import FileEntry, RenderMode, ToolSpec
from reviewtool.state import ReviewSession
from reviewtool.tools import (
    ToolRunResult,
    normalize_textual_shortcut,
    run_tool_background,
    run_tool_output,
    run_tool_terminal_blocking,
)
from reviewtool.ui_tui.diff_pane import DiffOptionList
from reviewtool.vcs.base import VCSError

_RENDER_CYCLE: tuple[RenderMode, ...] = ("full", "word_diff", "simple")
_THEME_CYCLE: tuple[str, ...] = tuple(p[0] for p in COLOR_SCHEME_PRESETS)


class ToolOutputModal(ModalScreen[None]):
    """Modal screen showing captured stdout/stderr from an external tool."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, result: ToolRunResult) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        cmd_str = " ".join(self.result.argv)
        with Vertical(id="modal_box"):
            yield Label(
                f"Tool: {self.result.tool.name} | Exit: {self.result.returncode}\n"
                f"Command: {cmd_str}"
            )
            log = RichLog(highlight=True, markup=False, id="modal_log")
            yield log
            yield Button("Close (Esc)", id="btn_close_modal", variant="primary")

    def on_mount(self) -> None:
        log = self.query_one("#modal_log", RichLog)
        if self.result.stdout:
            log.write(self.result.stdout.rstrip("\n"))
        if self.result.stderr:
            log.write("--- stderr ---")
            log.write(self.result.stderr.rstrip("\n"))
        if not self.result.stdout and not self.result.stderr:
            log.write("(no output)")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)


class ToolsMenuModal(ModalScreen[ToolSpec | None]):
    """Modal screen listing all configured external tools from tools.ini."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(self, tools: list[ToolSpec]) -> None:
        super().__init__()
        self.tools = tools

    def compose(self) -> ComposeResult:
        with Vertical(id="tools_modal_box"):
            yield Label("Launch External Tool (Enter to run, Esc to cancel):")
            opt_list = OptionList(id="tools_opt_list")
            for tool in self.tools:
                sc = f" [{tool.shortcut}]" if tool.shortcut else ""
                opt_list.add_option(
                    Option(f"{tool.name}{sc} — {tool.command} ({tool.mode})")
                )
            yield opt_list

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        idx = event.option_index
        if 0 <= idx < len(self.tools):
            self.dismiss(self.tools[idx])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class OptionsModal(ModalScreen[None]):
    """Interactive options modal for Textual TUI with proper form controls."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, session: ReviewSession) -> None:
        super().__init__()
        self.session = session
        self._ready = False

    def compose(self) -> ComposeResult:
        gen = self.session.config.general
        cur_theme = gen.theme.lower()
        if cur_theme == "dark":
            cur_theme = "vscode_dark"
        elif cur_theme == "light":
            cur_theme = "vscode_light"
        preset_ids = {p[0] for p in COLOR_SCHEME_PRESETS}
        if cur_theme not in preset_ids:
            cur_theme = "vscode_dark"

        with Vertical(id="options_modal_box"):
            yield Label("Options (saved immediately)", classes="section_title")
            with Horizontal(classes="opt_row"):
                yield Label("Render Mode:", classes="opt_label")
                yield Select(
                    [
                        ("Full (Dual Gutter + Word Diff + Syntax)", "full"),
                        ("Word Diff (Dual Gutter + Word Diff)", "word_diff"),
                        ("Simple (Single Gutter + Basic Colors)", "simple"),
                    ],
                    value=self.session.render_mode,
                    allow_blank=False,
                    id="sel_render_mode",
                )
            with Horizontal(classes="opt_row"):
                yield Label("Color Scheme:", classes="opt_label")
                yield Select(
                    [(label, pid) for pid, label, _s in COLOR_SCHEME_PRESETS],
                    value=cur_theme,
                    allow_blank=False,
                    id="sel_color_scheme",
                )
            with Horizontal(classes="opt_row"):
                yield Label("Context Lines:", classes="opt_label")
                yield Button("-", id="btn_opt_ctx_dec", classes="ctx_step_btn")
                yield Input(
                    str(self.session.context_lines),
                    type="integer",
                    id="inp_opt_context",
                )
                yield Button("+", id="btn_opt_ctx_inc", classes="ctx_step_btn")
            with Horizontal(classes="opt_row"):
                yield Checkbox(
                    "Full File Context",
                    value=self.session.full_file,
                    id="chk_opt_full_file",
                )
                yield Checkbox(
                    "Hide Viewed Files",
                    value=self.session.hide_viewed,
                    id="chk_opt_hide_viewed",
                )
            with Horizontal(classes="opt_footer"):
                yield Button(
                    "Close (Esc)", id="btn_close_options", variant="primary"
                )

    def on_mount(self) -> None:
        self._ready = True

    def on_select_changed(self, event: Select.Changed) -> None:
        if not self._ready or event.value == Select.BLANK:
            return
        if event.select.id == "sel_render_mode":
            self.session.set_render_mode(cast(RenderMode, str(event.value)))
        elif event.select.id == "sel_color_scheme":
            self.session.config.set_theme(str(event.value))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not self._ready:
            return
        if event.checkbox.id == "chk_opt_full_file":
            self.session.set_full_file(event.value)
        elif event.checkbox.id == "chk_opt_hide_viewed":
            self.session.set_hide_viewed(event.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self._ready or event.input.id != "inp_opt_context":
            return
        raw = event.value.strip()
        if raw.isdigit():
            self.session.set_context_lines(int(raw))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn_opt_ctx_dec":
            self.session.adjust_context_lines(-1)
            self._sync_ctx_input()
        elif btn_id == "btn_opt_ctx_inc":
            self.session.adjust_context_lines(1)
            self._sync_ctx_input()
        elif btn_id == "btn_close_options":
            self.dismiss(None)

    def _sync_ctx_input(self) -> None:
        self._ready = False
        inp = self.query_one("#inp_opt_context", Input)
        inp.value = str(self.session.context_lines)
        self._ready = True

    def action_close(self) -> None:
        self.dismiss(None)


class PaneSplitter(Static):
    """Draggable vertical splitter bar between the TUI file lists and diff pane."""

    def __init__(self) -> None:
        super().__init__("│\n" * 100, id="pane_splitter")
        self._dragging = False

    def on_mouse_down(self, event: MouseDown) -> None:
        self._dragging = True
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging:
            return
        app = self.app
        if isinstance(app, ReviewTuiApp):
            app.set_left_pane_width(event.screen_x)
        event.stop()

    def on_mouse_up(self, event: MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            event.stop()


class ReviewTuiApp(App[int]):
    """Textual TUI application for local pre-commit code review."""

    CSS = """
    #main_split {
        height: 1fr;
    }
    #left_pane {
        width: 44;
        min-width: 20;
        padding: 0 1;
    }
    #pane_splitter {
        width: 1;
        height: 1fr;
        color: $primary;
        background: $surface;
    }
    #pane_splitter:hover {
        color: $accent;
        background: $primary 30%;
    }
    #right_pane {
        width: 1fr;
        padding: 0 1;
    }
    .section_title {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }
    #list_unstaged, #list_staged {
        height: 1fr;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    #commit_input {
        height: 6;
    }
    #commit_buttons {
        height: 3;
        align: left middle;
    }
    #top_bar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    #diff_list {
        height: 1fr;
        text-wrap: nowrap;
        text-overflow: clip;
    }
    #loading_box {
        height: 3;
        align: center middle;
        background: $boost;
        color: $accent;
    }
    #loading_spinner {
        width: 16;
        height: 1;
    }
    #status_line {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    #modal_box, #tools_modal_box {
        width: 85%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        align: center middle;
    }
    #options_modal_box {
        width: 74;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    .opt_row {
        height: 3;
        align: left middle;
        margin-top: 1;
    }
    .opt_label {
        width: 18;
        content-align: left middle;
    }
    #sel_render_mode, #sel_color_scheme {
        width: 1fr;
    }
    #inp_opt_context {
        width: 12;
    }
    .ctx_step_btn {
        min-width: 5;
        width: 5;
    }
    .opt_footer {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    #modal_log, #tools_opt_list {
        height: 1fr;
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f5", "rescan", "Rescan"),
        Binding("n", "next_unstaged", "Next Unstaged"),
        Binding("p", "prev_unstaged", "Prev Unstaged"),
        Binding("s", "stage_item", "Stage File/Hunk"),
        Binding("u", "unstage_item", "Unstage File/Hunk"),
        Binding("r", "revert_item", "Revert File/Hunk"),
        Binding("v", "toggle_viewed", "Toggle Viewed"),
        Binding("h", "toggle_hide_viewed", "Hide Viewed"),
        Binding("f", "toggle_full_file", "Full File"),
        Binding("m", "cycle_render_mode", "Render Mode"),
        Binding("c", "cycle_color_scheme", "Color Scheme"),
        Binding("ctrl+c", "copy_diff_text", "Copy Diff"),
        Binding(" left_square_bracket", "dec_context", "-Context"),
        Binding("right_square_bracket", "inc_context", "+Context"),
        Binding("less_than_sign", "narrow_left_pane", "Narrow Lists"),
        Binding("greater_than_sign", "widen_left_pane", "Widen Lists"),
        Binding("z", "toggle_zoom_diff", "Zoom Diff"),
        Binding("t", "open_tools_menu", "Tools"),
        Binding("o", "open_options_modal", "Options"),
        Binding("space", "toggle_line_or_viewed", "Select Line/Viewed"),
    ]

    def __init__(self, session: ReviewSession) -> None:
        super().__init__()
        self.session = session
        self._unstaged_entries: list[FileEntry] = []
        self._staged_entries: list[FileEntry] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="top_bar")
        with Horizontal(id="main_split"):
            with Vertical(id="left_pane"):
                yield Label(
                    "Unstaged Changes (0)",
                    id="lbl_unstaged",
                    classes="section_title",
                )
                yield OptionList(id="list_unstaged")
                yield Label(
                    "Staged Changes (0)",
                    id="lbl_staged",
                    classes="section_title",
                )
                yield OptionList(id="list_staged")
                yield Label("Commit Message:", classes="section_title")
                yield TextArea(id="commit_input")
                with Horizontal(id="commit_buttons"):
                    if self.session.vcs.supports_amend:
                        yield Checkbox("Amend", id="chk_amend")
                    yield Button("Sign Off", id="btn_signoff")
                    yield Button("Commit", id="btn_commit", variant="success")
                    if self.session.vcs.supports_push:
                        yield Button("Push", id="btn_push", variant="primary")
            yield PaneSplitter()
            with Vertical(id="right_pane"):
                yield Label(
                    "No file selected",
                    id="lbl_diff_header",
                    classes="section_title",
                )
                with Horizontal(id="loading_box"):
                    yield LoadingIndicator(id="loading_spinner")
                    yield Label(
                        " Scanning repository status...", id="loading_label"
                    )
                yield DiffOptionList(id="diff_list")
        yield Static("", id="status_line")
        yield Footer()

    def on_mount(self) -> None:
        self.title = (
            f"reviewtool ({self.session.vcs.name}: "
            f"{self.session.vcs.repo_root.name})"
        )
        left_pane = self.query_one("#left_pane", Vertical)
        left_pane.styles.width = self.session.config.general.tui_left_width
        self.action_rescan()

    def _set_loading(self, active: bool, text: str = "") -> None:
        box = self.query_one("#loading_box", Horizontal)
        box.display = active
        if text:
            self.query_one("#loading_label", Label).update(f" {text}")
            self.query_one("#status_line", Static).update(text)

    def _finish_async_rescan(self) -> None:
        self._set_loading(False)
        self.title = (
            f"reviewtool ({self.session.vcs.name}: "
            f"{self.session.vcs.repo_root.name} @ {self.session.branch_name})"
        )
        self._refresh_all()
        self.query_one("#diff_list", DiffOptionList).focus()

    def _fail_async_rescan(self, err_msg: str) -> None:
        self._set_loading(False)
        self.notify(err_msg, severity="error")

    def set_left_pane_width(self, width: int) -> None:
        """Resize the TUI left pane and persist `tui_left_width` to config.ini ASAP."""
        max_w = max(30, (self.size.width - 25) if self.size.width > 0 else 120)
        clamped = max(20, min(max_w, width))
        left_pane = self.query_one("#left_pane", Vertical)
        if not left_pane.display:
            left_pane.display = True
            self.query_one("#pane_splitter", PaneSplitter).display = True
        left_pane.styles.width = clamped
        self.session.config.set_tui_left_width(clamped)
        self._refresh_diff()

    def action_narrow_left_pane(self) -> None:
        cur = self.session.config.general.tui_left_width
        self.set_left_pane_width(cur - 4)

    def action_widen_left_pane(self) -> None:
        cur = self.session.config.general.tui_left_width
        self.set_left_pane_width(cur + 4)

    def action_toggle_zoom_diff(self) -> None:
        left_pane = self.query_one("#left_pane", Vertical)
        splitter = self.query_one("#pane_splitter", PaneSplitter)
        show = not left_pane.display
        left_pane.display = show
        splitter.display = show
        self._refresh_diff()

    def _refresh_all(self) -> None:
        self._refresh_top_bar()
        self._refresh_lists()
        self._refresh_diff()

    def _refresh_top_bar(self) -> None:
        top = self.query_one("#top_bar", Static)
        full_tag = "ON" if self.session.full_file else "OFF"
        hide_tag = "ON" if self.session.hide_viewed else "OFF"
        top.update(
            f"Context: {self.session.context_lines} ([ / ]) | "
            f"Full (f): {full_tag} | "
            f"Hide Viewed (h): {hide_tag} | "
            f"Options (o) | "
            f"Tools (t)"
        )

    def _refresh_lists(self) -> None:
        self._unstaged_entries = self.session.visible_unstaged()
        self._staged_entries = self.session.visible_staged()

        lbl_u = self.query_one("#lbl_unstaged", Label)
        lbl_s = self.query_one("#lbl_staged", Label)
        u_vis = len(self._unstaged_entries)
        s_vis = len(self._staged_entries)
        lbl_u.update(
            f"Unstaged Changes ({u_vis}/{len(self.session.unstaged)})"
            if self.session.hide_viewed
            else f"Unstaged Changes ({u_vis})"
        )
        lbl_s.update(
            f"Staged Changes ({s_vis}/{len(self.session.staged)})"
            if self.session.hide_viewed
            else f"Staged Changes ({s_vis})"
        )

        list_u = self.query_one("#list_unstaged", OptionList)
        list_s = self.query_one("#list_staged", OptionList)
        self._populate_option_list(list_u, self._unstaged_entries)
        self._populate_option_list(list_s, self._staged_entries)

    def _populate_option_list(
        self, widget: OptionList, entries: list[FileEntry]
    ) -> None:
        widget.clear_options()
        cur = self.session.current_entry
        sel_idx: int | None = None
        for idx, entry in enumerate(entries):
            chk = "[x]" if self.session.is_viewed(entry.path) else "[ ]"
            style = "dim" if self.session.is_viewed(entry.path) else ""
            widget.add_option(
                Option(Text(f"{chk} {entry.status} {entry.path}", style=style))
            )
            if (
                cur is not None
                and cur.path == entry.path
                and cur.staged == entry.staged
            ):
                sel_idx = idx
        if sel_idx is not None:
            widget.highlighted = sel_idx

    def _refresh_diff(self) -> None:
        hdr = self.query_one("#lbl_diff_header", Label)
        diff_list = self.query_one("#diff_list", DiffOptionList)
        entry = self.session.current_entry
        if entry is None:
            hdr.update("No file selected")
        else:
            stage_str = "Staged" if entry.staged else "Unstaged"
            v_str = " [Viewed]" if self.session.is_viewed(entry.path) else ""
            hdr.update(f"{entry.path} ({stage_str}, {entry.status}){v_str}")

        dark = self.session.config.colors.is_dark
        diff_list.render_file_diff(
            self.session.current_diff,
            render_mode=self.session.render_mode,
            colors=self.session.config.colors,
            dark_theme=dark,
        )
        self._update_cursor_from_diff()

    def _update_cursor_from_diff(self) -> None:
        diff_list = self.query_one("#diff_list", DiffOptionList)
        row = diff_list.current_row()
        cur = self.session.cursor
        if row is not None and row.diff_line is not None:
            cur.old_line = row.diff_line.old_lineno
            cur.new_line = row.diff_line.new_lineno
            cur.line = cur.new_line or cur.old_line or 1
            cur.line_text = row.raw_code
            cur.col = diff_list.word_col
            cur.word = diff_list.current_word()
        status = self.query_one("#status_line", Static)
        status.update(
            f"File: {cur.file} | Line: {cur.line} | Word: '{cur.word}' | "
            f"Space: toggle viewed / select diff line | Enter: expand gap"
        )

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        widget_id = event.option_list.id
        idx = event.option_index
        if widget_id == "list_unstaged" and 0 <= idx < len(self._unstaged_entries):
            entry = self._unstaged_entries[idx]
            if self.session.current_entry != entry:
                self.session.select_entry(entry)
                self._refresh_diff()
        elif widget_id == "list_staged" and 0 <= idx < len(self._staged_entries):
            entry = self._staged_entries[idx]
            if self.session.current_entry != entry:
                self.session.select_entry(entry)
                self._refresh_diff()
        elif widget_id == "diff_list":
            diff_list = self.query_one("#diff_list", DiffOptionList)
            if diff_list._active_row_idx != idx:
                diff_list.refresh_active_cursor()
            self._update_cursor_from_diff()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id == "diff_list":
            diff_list = self.query_one("#diff_list", DiffOptionList)
            row = diff_list.current_row()
            if (
                row is not None
                and row.row_kind == "divider"
                and row.divider is not None
            ):
                self.session.expand_gap(row.divider)
                self._refresh_diff()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "chk_amend":
            if event.value:
                commit_input = self.query_one("#commit_input", TextArea)
                if not commit_input.text.strip():
                    last_msg = self.session.vcs.get_last_commit_message()
                    if last_msg:
                        commit_input.text = last_msg
            try:
                self.session.set_amend(event.value)
            except VCSError as exc:
                self.notify(str(exc), severity="error")
                return
            self._refresh_all()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn_signoff":
            commit_input = self.query_one("#commit_input", TextArea)
            line = self.session.vcs.get_signoff_line()
            cur_text = commit_input.text.rstrip()
            if line not in cur_text:
                sep = "\n\n" if cur_text else ""
                commit_input.text = f"{cur_text}{sep}{line}\n"
        elif btn_id == "btn_commit":
            self._perform_commit()
        elif btn_id == "btn_push":
            self._perform_push()

    def _perform_push(self) -> None:
        try:
            out = self.session.push()
        except VCSError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(
            f"Pushed: {out.splitlines()[0] if out else 'OK'}",
            severity="information",
        )

    def _perform_commit(self) -> None:
        commit_input = self.query_one("#commit_input", TextArea)
        amend = False
        if self.session.vcs.supports_amend:
            chk = self.query_one("#chk_amend", Checkbox)
            amend = chk.value
        try:
            out = self.session.commit(commit_input.text, amend=amend)
        except VCSError as exc:
            self.notify(str(exc), severity="error")
            return
        commit_input.text = ""
        if self.session.vcs.supports_amend:
            self.query_one("#chk_amend", Checkbox).value = False
        self._refresh_all()
        self.notify(
            f"Committed: {out.splitlines()[0] if out else 'OK'}",
            severity="information",
        )

    def action_rescan(self) -> None:
        self._set_loading(True, "Scanning repository status...")

        def _worker() -> None:
            try:
                self.session.rescan()
            except VCSError as exc:
                self.call_from_thread(self._fail_async_rescan, str(exc))
                return
            self.call_from_thread(self._finish_async_rescan)

        self.run_worker(_worker, thread=True, exclusive=True)

    def action_next_unstaged(self) -> None:
        if self.session.select_adjacent_unstaged(1) is not None:
            self._refresh_lists()
            self._refresh_diff()
            self.query_one("#diff_list", DiffOptionList).focus()

    def action_prev_unstaged(self) -> None:
        if self.session.select_adjacent_unstaged(-1) is not None:
            self._refresh_lists()
            self._refresh_diff()
            self.query_one("#diff_list", DiffOptionList).focus()

    def action_stage_item(self) -> None:
        entry = self.session.current_entry
        if entry is None:
            return
        focused = self.focused
        diff_list = self.query_one("#diff_list", DiffOptionList)
        if focused is diff_list and not entry.staged:
            row = diff_list.current_row()
            if row is not None and row.hunk_index is not None:
                sel = diff_list.selected_lines_by_hunk.get(row.hunk_index)
                try:
                    self.session.stage_hunk_or_lines(
                        row.hunk_index, sel if sel else None
                    )
                except VCSError as exc:
                    self.notify(str(exc), severity="error")
                    return
                self._refresh_all()
                return
        if not entry.staged:
            try:
                self.session.stage_file(entry.path)
            except VCSError as exc:
                self.notify(str(exc), severity="error")
                return
            self._refresh_all()

    def action_unstage_item(self) -> None:
        entry = self.session.current_entry
        if entry is None:
            return
        focused = self.focused
        diff_list = self.query_one("#diff_list", DiffOptionList)
        if focused is diff_list and entry.staged:
            row = diff_list.current_row()
            if row is not None and row.hunk_index is not None:
                sel = diff_list.selected_lines_by_hunk.get(row.hunk_index)
                try:
                    self.session.unstage_hunk_or_lines(
                        row.hunk_index, sel if sel else None
                    )
                except VCSError as exc:
                    self.notify(str(exc), severity="error")
                    return
                self._refresh_all()
                return
        if entry.staged:
            try:
                self.session.unstage_file(entry.path)
            except VCSError as exc:
                self.notify(str(exc), severity="error")
                return
            self._refresh_all()

    def action_revert_item(self) -> None:
        entry = self.session.current_entry
        if entry is None:
            return
        focused = self.focused
        diff_list = self.query_one("#diff_list", DiffOptionList)
        try:
            if focused is diff_list:
                row = diff_list.current_row()
                if row is not None and row.hunk_index is not None:
                    sel = diff_list.selected_lines_by_hunk.get(row.hunk_index)
                    self.session.revert_hunk_or_lines(
                        row.hunk_index, sel if sel else None
                    )
                    self._refresh_all()
                    return
            self.session.revert_file(entry)
        except VCSError as exc:
            self.notify(str(exc), severity="error")
            return
        self._refresh_all()

    def action_toggle_viewed(self) -> None:
        if self.session.current_entry is None:
            return
        self.session.toggle_viewed(self.session.current_entry.path)
        self._refresh_lists()
        self._refresh_diff()

    def action_toggle_line_or_viewed(self) -> None:
        diff_list = self.query_one("#diff_list", DiffOptionList)
        if self.focused is diff_list:
            if diff_list.toggle_current_line_selection():
                return
        self.action_toggle_viewed()

    def action_toggle_hide_viewed(self) -> None:
        self.session.set_hide_viewed(not self.session.hide_viewed)
        self._refresh_top_bar()
        self._refresh_lists()

    def action_toggle_full_file(self) -> None:
        self.session.set_full_file(not self.session.full_file)
        self._refresh_all()

    def action_cycle_render_mode(self) -> None:
        cur_idx = _RENDER_CYCLE.index(self.session.render_mode)
        next_mode = _RENDER_CYCLE[(cur_idx + 1) % len(_RENDER_CYCLE)]
        self.session.set_render_mode(cast(RenderMode, next_mode))
        self._refresh_top_bar()
        self._refresh_diff()

    def action_cycle_color_scheme(self) -> None:
        cur = self.session.config.general.theme.lower()
        idx = _THEME_CYCLE.index(cur) if cur in _THEME_CYCLE else 0
        next_theme = _THEME_CYCLE[(idx + 1) % len(_THEME_CYCLE)]
        self.session.config.set_theme(next_theme)
        self._refresh_top_bar()
        self._refresh_diff()

    def action_copy_diff_text(self) -> None:
        diff_list = self.query_one("#diff_list", DiffOptionList)
        text = diff_list.copyable_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify("Copied diff text to clipboard")

    def action_dec_context(self) -> None:
        self.session.adjust_context_lines(-1)
        self._refresh_top_bar()
        self._refresh_diff()

    def action_inc_context(self) -> None:
        self.session.adjust_context_lines(1)
        self._refresh_top_bar()
        self._refresh_diff()

    def action_open_tools_menu(self) -> None:
        self.push_screen(
            ToolsMenuModal(self.session.config.tools),
            self._on_tool_chosen,
        )

    def action_open_options_modal(self) -> None:
        self.push_screen(
            OptionsModal(self.session),
            lambda _: self._refresh_all(),
        )

    def _on_tool_chosen(self, tool: ToolSpec | None) -> None:
        if tool is not None:
            self._execute_tool(tool)

    def on_key(self, event: Key) -> None:
        if isinstance(self.focused, TextArea):
            return
        diff_list = self.query_one("#diff_list", DiffOptionList)
        if self.focused is diff_list and event.key in (
            "left",
            "right",
            "home",
            "end",
            "ctrl+left",
            "ctrl+right",
        ):
            self.call_after_refresh(self._update_cursor_from_diff)
            return

        for tool in self.session.config.tools:
            if not tool.shortcut:
                continue
            norm = normalize_textual_shortcut(tool.shortcut)
            if event.key == norm or event.character == tool.shortcut:
                diff_list = self.query_one("#diff_list", DiffOptionList)
                if self.focused is diff_list or "+" in norm:
                    event.stop()
                    self._execute_tool(tool)
                    return

    def _execute_tool(self, tool: ToolSpec) -> None:
        argv = self.session.build_tool_argv(tool)
        if not argv:
            return
        cwd = self.session.vcs.repo_root

        try:
            if tool.mode == "output":
                res = run_tool_output(tool, argv, cwd)
                self.push_screen(ToolOutputModal(res))
            elif tool.mode == "terminal":
                with self.suspend():
                    run_tool_terminal_blocking(tool, argv, cwd)
            else:
                run_tool_background(tool, argv, cwd)
                self.notify(f"Started: {tool.name}")
        except FileNotFoundError as exc:
            self.notify(f"Tool not found: {exc}", severity="error")
            return

        if tool.refresh_after:
            if self.session.current_entry is not None:
                self.session.refresh_single_file(
                    self.session.current_entry.path
                )
                self._refresh_all()
            else:
                self.action_rescan()


def run_tui_app(session: ReviewSession) -> int:
    """Launch the Textual TUI frontend."""
    app = ReviewTuiApp(session)
    app.run()
    return 0
