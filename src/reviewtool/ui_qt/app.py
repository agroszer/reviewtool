"""PyQt6 main application window for reviewtool."""

import os
import sys
from typing import cast

from PyQt6.QtCore import QPoint, QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QFont,
    QKeySequence,
    QMoveEvent,
    QResizeEvent,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from reviewtool.models import ExpandDivider, FileEntry, ToolSpec
from reviewtool.state import ReviewSession
from reviewtool.tools import (
    find_terminal_Launcher,
    run_tool_output,
    wrap_argv_with_cd,
)
from reviewtool.ui_qt.dialogs import (
    OptionsDialog,
    ToolOutputDialog,
    confirm_action,
)
from reviewtool.ui_qt.diff_view import DiffViewWidget
from reviewtool.vcs.base import VCSError


class RescanWorker(QThread):
    """Background worker thread for full repository status rescan."""

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, session: ReviewSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session

    def run(self) -> None:
        try:
            self.session.rescan()
        except VCSError as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit()


class MainWindow(QMainWindow):
    """Main PyQt6 window with two-pane staging, Viewed checkboxes, and inline diff."""

    def __init__(self, session: ReviewSession) -> None:
        super().__init__()
        self.session = session
        self._restoring_ui = True
        self._updating_lists = False
        self._bg_processes: list[QProcess] = []
        self._rescan_worker: RescanWorker | None = None

        self.setWindowTitle(
            f"reviewtool — {session.vcs.repo_root.name} ({session.vcs.name})"
        )

        self._build_ui()
        self._apply_fonts()
        self._restore_geometry_from_config()
        self._bind_global_shortcuts()
        self.diff_view.bind_tools(self.session.config.tools)
        self._restoring_ui = False
        self.diff_view.setFocus()
        QTimer.singleShot(0, self._on_rescan)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self.btn_rescan = QPushButton("Rescan (F5)")
        self.btn_rescan.clicked.connect(self._on_rescan)
        toolbar.addWidget(self.btn_rescan)

        toolbar.addWidget(QLabel("Context:"))
        self.btn_ctx_minus = QPushButton("-")
        self.btn_ctx_minus.setFixedWidth(28)
        self.btn_ctx_minus.clicked.connect(lambda: self._on_adjust_context(-1))
        toolbar.addWidget(self.btn_ctx_minus)

        self.spin_context = QSpinBox()
        self.spin_context.setRange(0, 999)
        self.spin_context.setValue(self.session.context_lines)
        self.spin_context.valueChanged.connect(self._on_context_spin_changed)
        toolbar.addWidget(self.spin_context)

        self.btn_ctx_plus = QPushButton("+")
        self.btn_ctx_plus.setFixedWidth(28)
        self.btn_ctx_plus.clicked.connect(lambda: self._on_adjust_context(1))
        toolbar.addWidget(self.btn_ctx_plus)

        self.chk_full_file = QCheckBox("Full File")
        self.chk_full_file.setChecked(self.session.full_file)
        self.chk_full_file.toggled.connect(self._on_full_file_toggled)
        toolbar.addWidget(self.chk_full_file)

        self.chk_hide_viewed = QCheckBox("Hide Viewed")
        self.chk_hide_viewed.setChecked(self.session.hide_viewed)
        self.chk_hide_viewed.toggled.connect(self._on_hide_viewed_toggled)
        toolbar.addWidget(self.chk_hide_viewed)

        self.btn_tools = QPushButton("External Tools")
        self.tools_menu = QMenu(self)
        self.btn_tools.setMenu(self.tools_menu)
        self._populate_tools_menu()
        toolbar.addWidget(self.btn_tools)

        self.btn_options = QPushButton("Options...")
        self.btn_options.clicked.connect(self._on_open_options)
        toolbar.addWidget(self.btn_options)

        toolbar.addStretch(1)
        root_layout.addLayout(toolbar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical, self.main_splitter)
        self.left_splitter.splitterMoved.connect(self._on_splitter_moved)

        unstaged_box = QWidget()
        u_layout = QVBoxLayout(unstaged_box)
        u_layout.setContentsMargins(0, 0, 0, 0)
        u_hdr = QHBoxLayout()
        self.lbl_unstaged = QLabel("<b>Unstaged Changes (0)</b>")
        u_hdr.addWidget(self.lbl_unstaged)
        u_hdr.addStretch(1)
        self.btn_stage_file = QPushButton("Stage")
        self.btn_stage_file.clicked.connect(self._on_stage_selected_file)
        u_hdr.addWidget(self.btn_stage_file)
        self.btn_stage_all = QPushButton("Stage All")
        self.btn_stage_all.clicked.connect(self._on_stage_all)
        u_hdr.addWidget(self.btn_stage_all)
        u_layout.addLayout(u_hdr)

        self.list_unstaged = QListWidget()
        self.list_unstaged.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.list_unstaged.currentItemChanged.connect(
            self._on_unstaged_selection_changed
        )
        self.list_unstaged.itemClicked.connect(self._on_unstaged_item_clicked)
        self.list_unstaged.itemChanged.connect(self._on_list_item_changed)
        self.list_unstaged.itemDoubleClicked.connect(
            self._on_unstaged_double_clicked
        )
        self.list_unstaged.customContextMenuRequested.connect(
            self._on_unstaged_context_menu
        )
        u_layout.addWidget(self.list_unstaged)
        self.left_splitter.addWidget(unstaged_box)

        staged_box = QWidget()
        s_layout = QVBoxLayout(staged_box)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_hdr = QHBoxLayout()
        self.lbl_staged = QLabel("<b>Staged Changes (0)</b>")
        s_hdr.addWidget(self.lbl_staged)
        s_hdr.addStretch(1)
        self.btn_unstage_file = QPushButton("Unstage")
        self.btn_unstage_file.clicked.connect(self._on_unstage_selected_file)
        s_hdr.addWidget(self.btn_unstage_file)
        self.btn_unstage_all = QPushButton("Unstage All")
        self.btn_unstage_all.clicked.connect(self._on_unstage_all)
        s_hdr.addWidget(self.btn_unstage_all)
        s_layout.addLayout(s_hdr)

        self.list_staged = QListWidget()
        self.list_staged.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.list_staged.currentItemChanged.connect(
            self._on_staged_selection_changed
        )
        self.list_staged.itemClicked.connect(self._on_staged_item_clicked)
        self.list_staged.itemChanged.connect(self._on_list_item_changed)
        self.list_staged.itemDoubleClicked.connect(
            self._on_staged_double_clicked
        )
        self.list_staged.customContextMenuRequested.connect(
            self._on_staged_context_menu
        )
        s_layout.addWidget(self.list_staged)
        self.left_splitter.addWidget(staged_box)

        commit_box = QWidget()
        c_layout = QVBoxLayout(commit_box)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.addWidget(QLabel("<b>Commit Message:</b>"))
        self.txt_commit = QPlainTextEdit()
        self.txt_commit.setPlaceholderText("Enter commit message...")
        c_layout.addWidget(self.txt_commit)

        c_btns = QHBoxLayout()
        self.chk_amend = QCheckBox("Amend Last Commit")
        self.chk_amend.setVisible(self.session.vcs.supports_amend)
        self.chk_amend.toggled.connect(self._on_amend_toggled)
        c_btns.addWidget(self.chk_amend)

        self.btn_signoff = QPushButton("Sign Off")
        self.btn_signoff.clicked.connect(self._on_signoff)
        c_btns.addWidget(self.btn_signoff)

        c_btns.addStretch(1)
        self.btn_commit = QPushButton("Commit")
        self.btn_commit.clicked.connect(self._on_commit)
        c_btns.addWidget(self.btn_commit)

        self.btn_push = QPushButton("Push")
        self.btn_push.setVisible(self.session.vcs.supports_push)
        self.btn_push.clicked.connect(self._on_push)
        c_btns.addWidget(self.btn_push)
        c_layout.addLayout(c_btns)
        self.left_splitter.addWidget(commit_box)

        right_box = QWidget()
        r_layout = QVBoxLayout(right_box)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_hdr = QHBoxLayout()
        self.lbl_file_header = QLabel("<b>No file selected</b>")
        r_hdr.addWidget(self.lbl_file_header)
        r_hdr.addStretch(1)

        self.btn_stage_hunk = QPushButton("Stage Hunk")
        self.btn_stage_hunk.clicked.connect(self._on_btn_stage_hunk)
        r_hdr.addWidget(self.btn_stage_hunk)

        self.btn_stage_lines = QPushButton("Stage Lines")
        self.btn_stage_lines.clicked.connect(self._on_btn_stage_lines)
        r_hdr.addWidget(self.btn_stage_lines)

        self.btn_revert_hunk = QPushButton("Revert Hunk")
        self.btn_revert_hunk.clicked.connect(self._on_btn_revert_hunk)
        r_hdr.addWidget(self.btn_revert_hunk)

        r_layout.addLayout(r_hdr)

        self.diff_view = DiffViewWidget(self)
        self.diff_view.on_cursor_changed = self._on_diff_cursor_changed
        self.diff_view.on_expand_divider = self._on_expand_divider
        self.diff_view.on_stage_hunk = self._on_stage_hunk
        self.diff_view.on_unstage_hunk = self._on_unstage_hunk
        self.diff_view.on_revert_hunk = self._on_revert_hunk
        self.diff_view.on_stage_lines = self._on_stage_lines
        self.diff_view.on_unstage_lines = self._on_unstage_lines
        self.diff_view.on_revert_lines = self._on_revert_lines
        self.diff_view.on_run_tool = self._run_external_tool
        r_layout.addWidget(self.diff_view)

        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(right_box)
        root_layout.addWidget(self.main_splitter, stretch=1)

        self.status_bar = QStatusBar(self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Scanning repo...")
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.setStatusBar(self.status_bar)

    def _restore_geometry_from_config(self) -> None:
        geo = self.session.config.qt_geometry
        self.resize(geo.window_width, geo.window_height)
        if geo.window_x is not None and geo.window_y is not None:
            self.move(geo.window_x, geo.window_y)
        if len(geo.main_splitter) == 2:
            self.main_splitter.setSizes(geo.main_splitter)
        if len(geo.left_splitter) == 3:
            self.left_splitter.setSizes(geo.left_splitter)
        if geo.window_maximized:
            self.setWindowState(
                self.windowState() | Qt.WindowState.WindowMaximized
            )

    def _save_geometry_asap(self) -> None:
        if self._restoring_ui:
            return
        is_max = bool(self.windowState() & Qt.WindowState.WindowMaximized)
        pos = self.pos()
        size = self.size()
        self.session.config.update_qt_geometry(
            x=pos.x() if not is_max else None,
            y=pos.y() if not is_max else None,
            width=size.width() if not is_max else None,
            height=size.height() if not is_max else None,
            maximized=is_max,
            main_splitter=self.main_splitter.sizes(),
            left_splitter=self.left_splitter.sizes(),
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._save_geometry_asap()

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        self._save_geometry_asap()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_geometry_asap()
        super().closeEvent(event)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        self._save_geometry_asap()

    def _bind_global_shortcuts(self) -> None:
        sc_rescan = QShortcut(QKeySequence("F5"), self)
        sc_rescan.activated.connect(self._on_rescan)
        sc_ctx_dec = QShortcut(QKeySequence("["), self)
        sc_ctx_dec.activated.connect(lambda: self._on_adjust_context(-1))
        sc_ctx_inc = QShortcut(QKeySequence("]"), self)
        sc_ctx_inc.activated.connect(lambda: self._on_adjust_context(1))
        for seq in ("n", "Alt+Down", "Ctrl+Down"):
            sc_next = QShortcut(QKeySequence(seq), self)
            sc_next.activated.connect(lambda: self._on_step_unstaged(1))
        for seq in ("p", "Alt+Up", "Ctrl+Up"):
            sc_prev = QShortcut(QKeySequence(seq), self)
            sc_prev.activated.connect(lambda: self._on_step_unstaged(-1))

    def _on_step_unstaged(self, delta: int) -> None:
        if self.txt_commit.hasFocus():
            return
        entry = self.session.select_adjacent_unstaged(delta)
        if entry is not None:
            self._refresh_file_lists()
            self._refresh_diff_pane()
            self.diff_view.setFocus()

    def _populate_tools_menu(self) -> None:
        self.tools_menu.clear()
        for tool in self.session.config.tools:
            hint = f" [{tool.shortcut}]" if tool.shortcut else ""
            act = QAction(f"{tool.name}{hint}", self)
            act.triggered.connect(self._make_menu_tool_cb(tool))
            self.tools_menu.addAction(act)

    def _make_menu_tool_cb(self, tool: ToolSpec):
        def _run() -> None:
            self._run_external_tool(tool)

        return _run

    def _refresh_file_lists(self) -> None:
        self._updating_lists = True
        cur_entry = self.session.current_entry

        vis_unstaged = self.session.visible_unstaged()
        vis_staged = self.session.visible_staged()

        u_total = len(self.session.unstaged)
        s_total = len(self.session.staged)
        self.lbl_unstaged.setText(
            f"<b>Unstaged Changes ({len(vis_unstaged)}/{u_total})</b>"
            if self.session.hide_viewed
            else f"<b>Unstaged Changes ({u_total})</b>"
        )
        self.lbl_staged.setText(
            f"<b>Staged Changes ({len(vis_staged)}/{s_total})</b>"
            if self.session.hide_viewed
            else f"<b>Staged Changes ({s_total})</b>"
        )

        self._populate_list_widget(self.list_unstaged, vis_unstaged, cur_entry)
        self._populate_list_widget(self.list_staged, vis_staged, cur_entry)
        self._updating_lists = False

    def _populate_list_widget(
        self,
        widget: QListWidget,
        entries: list[FileEntry],
        selected_entry: FileEntry | None,
    ) -> None:
        widget.clear()
        select_row = -1
        for idx, entry in enumerate(entries):
            label = f"[{entry.status}] {entry.path}"
            item = QListWidgetItem(label)
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            is_viewed = self.session.is_viewed(entry.path)
            item.setCheckState(
                Qt.CheckState.Checked if is_viewed else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(
                f"{entry.path} (status: {entry.status}, checkbox = Viewed)"
            )
            widget.addItem(item)
            if (
                selected_entry is not None
                and selected_entry.path == entry.path
                and selected_entry.staged == entry.staged
            ):
                select_row = idx
        if select_row >= 0:
            widget.setCurrentRow(select_row)

    def _refresh_diff_pane(self) -> None:
        entry = self.session.current_entry
        if entry is None:
            self.lbl_file_header.setText("<b>No file selected</b>")
            self.btn_stage_hunk.setEnabled(False)
            self.btn_stage_lines.setEnabled(False)
            self.btn_revert_hunk.setEnabled(False)
        else:
            kind_str = "Staged" if entry.staged else "Unstaged"
            viewed_badge = (
                " [Viewed]" if self.session.is_viewed(entry.path) else ""
            )
            self.lbl_file_header.setText(
                f"<b>{entry.path}</b> "
                f"({kind_str}, status: {entry.status}){viewed_badge}"
            )
            self.btn_stage_hunk.setText(
                "Unstage Hunk" if entry.staged else "Stage Hunk"
            )
            self.btn_stage_lines.setText(
                "Unstage Lines" if entry.staged else "Stage Lines"
            )
            self.btn_stage_hunk.setEnabled(True)
            self.btn_stage_lines.setEnabled(True)
            self.btn_revert_hunk.setEnabled(True)

        dark = self.session.config.colors.is_dark
        self.diff_view.render_diff(
            self.session.current_diff,
            render_mode=self.session.render_mode,
            is_staged=bool(entry and entry.staged),
            dark_theme=dark,
            colors=self.session.config.colors,
        )

    def _on_unstaged_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if self._updating_lists or current is None:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry):
            self._updating_lists = True
            self.list_staged.clearSelection()
            self._updating_lists = False
            self.session.select_entry(entry)
            self._refresh_diff_pane()

    def _on_staged_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if self._updating_lists or current is None:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry):
            self._updating_lists = True
            self.list_unstaged.clearSelection()
            self._updating_lists = False
            self.session.select_entry(entry)
            self._refresh_diff_pane()

    def _on_unstaged_item_clicked(
        self, item: QListWidgetItem | None
    ) -> None:
        if self._updating_lists or item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry) and self.session.current_entry != entry:
            self.session.select_entry(entry)
            self._refresh_diff_pane()

    def _on_staged_item_clicked(
        self, item: QListWidgetItem | None
    ) -> None:
        if self._updating_lists or item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry) and self.session.current_entry != entry:
            self.session.select_entry(entry)
            self._refresh_diff_pane()

    def _on_list_item_changed(self, item: QListWidgetItem | None) -> None:
        if self._updating_lists or item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, FileEntry):
            return
        checked = item.checkState() == Qt.CheckState.Checked
        self.session.set_viewed(entry.path, checked)
        if self.session.hide_viewed:
            self._refresh_file_lists()
        else:
            self._sync_viewed_checkstates(entry.path, checked)
        if (
            self.session.current_entry is not None
            and self.session.current_entry.path == entry.path
        ):
            self._refresh_diff_pane()

    def _sync_viewed_checkstates(
        self, rel_path: str, checked: bool
    ) -> None:
        self._updating_lists = True
        state = (
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        for widget in (self.list_unstaged, self.list_staged):
            for idx in range(widget.count()):
                w_item = widget.item(idx)
                if w_item is None:
                    continue
                w_entry = w_item.data(Qt.ItemDataRole.UserRole)
                if (
                    isinstance(w_entry, FileEntry)
                    and w_entry.path == rel_path
                    and w_item.checkState() != state
                ):
                    w_item.setCheckState(state)
        self._updating_lists = False

    def _on_unstaged_double_clicked(
        self, item: QListWidgetItem | None
    ) -> None:
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry):
            self._safe_vcs_call(lambda: self.session.stage_file(entry.path))

    def _on_staged_double_clicked(
        self, item: QListWidgetItem | None
    ) -> None:
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, FileEntry):
            self._safe_vcs_call(lambda: self.session.unstage_file(entry.path))

    def _on_stage_selected_file(self) -> None:
        item = self.list_unstaged.currentItem()
        if item is not None:
            self._on_unstaged_double_clicked(item)

    def _on_unstage_selected_file(self) -> None:
        item = self.list_staged.currentItem()
        if item is not None:
            self._on_staged_double_clicked(item)

    def _on_stage_all(self) -> None:
        self._safe_vcs_call(self.session.stage_all)

    def _on_unstage_all(self) -> None:
        self._safe_vcs_call(self.session.unstage_all)

    def _on_unstaged_context_menu(self, pos: QPoint) -> None:
        item = self.list_unstaged.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, FileEntry):
            return
        self._show_file_context_menu(
            self.list_unstaged.mapToGlobal(pos), entry
        )

    def _on_staged_context_menu(self, pos: QPoint) -> None:
        item = self.list_staged.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, FileEntry):
            return
        self._show_file_context_menu(
            self.list_staged.mapToGlobal(pos), entry
        )

    def _show_file_context_menu(
        self, global_pos: QPoint, entry: FileEntry
    ) -> None:
        menu = QMenu(self)
        if not entry.staged:
            act_stage = QAction("Stage File", menu)
            act_stage.triggered.connect(
                lambda: self._safe_vcs_call(
                    lambda: self.session.stage_file(entry.path)
                )
            )
            menu.addAction(act_stage)
        else:
            act_unstage = QAction("Unstage File", menu)
            act_unstage.triggered.connect(
                lambda: self._safe_vcs_call(
                    lambda: self.session.unstage_file(entry.path)
                )
            )
            menu.addAction(act_unstage)

        act_viewed = QAction(
            "Unmark Viewed"
            if self.session.is_viewed(entry.path)
            else "Mark Viewed",
            menu,
        )
        act_viewed.triggered.connect(
            lambda: self._toggle_file_viewed(entry.path)
        )
        menu.addAction(act_viewed)

        act_revert = QAction("Revert File Changes...", menu)
        act_revert.triggered.connect(lambda: self._confirm_revert_file(entry))
        menu.addAction(act_revert)

        menu.addSeparator()
        for tool in self.session.config.tools:
            act_t = QAction(tool.name, menu)
            act_t.triggered.connect(self._make_menu_tool_cb(tool))
            menu.addAction(act_t)

        menu.exec(global_pos)

    def _toggle_file_viewed(self, rel_path: str) -> None:
        self.session.toggle_viewed(rel_path)
        self._refresh_file_lists()
        self._refresh_diff_pane()

    def _confirm_revert_file(self, entry: FileEntry) -> None:
        action_desc = (
            f"Delete untracked file '{entry.path}'?"
            if entry.status == "?"
            else f"Discard all changes in '{entry.path}'?"
        )
        if confirm_action(self, "Confirm Revert File", action_desc):
            self._safe_vcs_call(lambda: self.session.revert_file(entry))

    def _on_rescan(self) -> None:
        if self._rescan_worker is not None and self._rescan_worker.isRunning():
            return
        self.btn_rescan.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_bar.showMessage("Scanning repository status...")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        self.diff_view.setPlainText("⏳ Scanning repository changes...")

        worker = RescanWorker(self.session, self)
        worker.succeeded.connect(self._on_rescan_succeeded)
        worker.failed.connect(self._on_rescan_failed)
        worker.finished.connect(worker.deleteLater)
        self._rescan_worker = worker
        worker.start()

    def _on_rescan_succeeded(self) -> None:
        QApplication.restoreOverrideCursor()
        self.progress_bar.setVisible(False)
        self.btn_rescan.setEnabled(True)
        self._rescan_worker = None
        self.setWindowTitle(
            f"reviewtool — {self.session.vcs.repo_root.name} "
            f"({self.session.vcs.name}: {self.session.branch_name})"
        )
        self._refresh_file_lists()
        self._refresh_diff_pane()
        self.status_bar.showMessage(
            f"Ready — {len(self.session.unstaged)} unstaged, "
            f"{len(self.session.staged)} staged."
        )
        self.diff_view.setFocus()

    def _on_rescan_failed(self, err_msg: str) -> None:
        QApplication.restoreOverrideCursor()
        self.progress_bar.setVisible(False)
        self.btn_rescan.setEnabled(True)
        self._rescan_worker = None
        QMessageBox.warning(self, "VCS Error", err_msg)
        self.status_bar.showMessage("Scan failed.")

    def _on_adjust_context(self, delta: int) -> None:
        new_val = max(0, self.session.context_lines + delta)
        self.spin_context.setValue(new_val)

    def _on_context_spin_changed(self, val: int) -> None:
        if self._restoring_ui:
            return
        self.session.set_context_lines(val)
        self._refresh_diff_pane()

    def _on_full_file_toggled(self, checked: bool) -> None:
        if self._restoring_ui:
            return
        self.session.set_full_file(checked)
        self._refresh_diff_pane()

    def _on_hide_viewed_toggled(self, checked: bool) -> None:
        if self._restoring_ui:
            return
        self.session.set_hide_viewed(checked)
        self._refresh_file_lists()

    def _on_open_options(self) -> None:
        dlg = OptionsDialog(
            session=self.session,
            on_changed=self._on_options_updated,
            parent=self,
        )
        dlg.exec()

    def _on_options_updated(self) -> None:
        self._restoring_ui = True
        self.chk_hide_viewed.setChecked(self.session.hide_viewed)
        self._restoring_ui = False
        self._apply_fonts()
        self._refresh_file_lists()
        self._refresh_diff_pane()

    def _apply_fonts(self) -> None:
        gen = self.session.config.general
        list_font = QFont(self.list_unstaged.font())
        list_font.setPointSize(gen.file_list_font_size)
        self.list_unstaged.setFont(list_font)
        self.list_staged.setFont(list_font)

        commit_font = QFont(self.txt_commit.font())
        commit_font.setPointSize(gen.commit_font_size)
        self.txt_commit.setFont(commit_font)

        self.diff_view.set_diff_font(gen.diff_font_family, gen.diff_font_size)

    def _on_diff_cursor_changed(
        self,
        line: int,
        old_line: int | None,
        new_line: int | None,
        col: int,
        word: str,
        selection: str,
        line_text: str,
    ) -> None:
        cur = self.session.cursor
        cur.line = line
        cur.old_line = old_line
        cur.new_line = new_line
        cur.col = col
        cur.word = word
        cur.selection = selection
        cur.line_text = line_text
        self.status_bar.showMessage(
            f"File: {cur.file} | Line: {line} (old: {old_line or '-'}, "
            f"new: {new_line or '-'}) | Col: {col} | Word: '{word}'"
        )

    def _on_expand_divider(self, divider: ExpandDivider) -> None:
        self.session.expand_gap(divider)
        self._refresh_diff_pane()

    def _on_btn_stage_hunk(self) -> None:
        hunk_idx, _ = self.diff_view.current_hunk_and_selected_lines()
        if hunk_idx is None:
            return
        if self.session.current_entry and self.session.current_entry.staged:
            self._on_unstage_hunk(hunk_idx)
        else:
            self._on_stage_hunk(hunk_idx)

    def _on_btn_stage_lines(self) -> None:
        hunk_idx, sel_lines = self.diff_view.current_hunk_and_selected_lines()
        if hunk_idx is None or not sel_lines:
            QMessageBox.information(
                self,
                "Select Lines",
                "Place cursor on or select +/- diff lines inside a hunk first.",
            )
            return
        if self.session.current_entry and self.session.current_entry.staged:
            self._on_unstage_lines(hunk_idx, sel_lines)
        else:
            self._on_stage_lines(hunk_idx, sel_lines)

    def _on_btn_revert_hunk(self) -> None:
        hunk_idx, _ = self.diff_view.current_hunk_and_selected_lines()
        if hunk_idx is not None:
            self._on_revert_hunk(hunk_idx)

    def _on_stage_hunk(self, hunk_index: int) -> None:
        self._safe_vcs_call(
            lambda: self.session.stage_hunk_or_lines(hunk_index, None)
        )

    def _on_unstage_hunk(self, hunk_index: int) -> None:
        self._safe_vcs_call(
            lambda: self.session.unstage_hunk_or_lines(hunk_index, None)
        )

    def _on_revert_hunk(self, hunk_index: int) -> None:
        if confirm_action(
            self, "Confirm Revert Hunk", "Discard changes in this hunk?"
        ):
            self._safe_vcs_call(
                lambda: self.session.revert_hunk_or_lines(hunk_index, None)
            )

    def _on_stage_lines(self, hunk_index: int, lines: set[int]) -> None:
        self._safe_vcs_call(
            lambda: self.session.stage_hunk_or_lines(hunk_index, lines)
        )

    def _on_unstage_lines(self, hunk_index: int, lines: set[int]) -> None:
        self._safe_vcs_call(
            lambda: self.session.unstage_hunk_or_lines(hunk_index, lines)
        )

    def _on_revert_lines(self, hunk_index: int, lines: set[int]) -> None:
        if confirm_action(
            self,
            "Confirm Revert Lines",
            f"Discard {len(lines)} selected line(s)?",
        ):
            self._safe_vcs_call(
                lambda: self.session.revert_hunk_or_lines(hunk_index, lines)
            )

    def _on_amend_toggled(self, checked: bool) -> None:
        if checked and not self.txt_commit.toPlainText().strip():
            last_msg = self.session.vcs.get_last_commit_message()
            if last_msg:
                self.txt_commit.setPlainText(last_msg)
        self._safe_vcs_call(lambda: self.session.set_amend(checked))

    def _on_signoff(self) -> None:
        line = self.session.vcs.get_signoff_line()
        cur_msg = self.txt_commit.toPlainText().rstrip()
        if line in cur_msg:
            return
        sep = "\n\n" if cur_msg else ""
        self.txt_commit.setPlainText(f"{cur_msg}{sep}{line}\n")

    def _on_commit(self) -> None:
        msg = self.txt_commit.toPlainText().strip()
        amend = self.chk_amend.isChecked()
        try:
            output = self.session.commit(msg, amend=amend)
        except VCSError as exc:
            QMessageBox.warning(self, "Commit Failed", str(exc))
            return

        self.txt_commit.clear()
        self.chk_amend.setChecked(False)
        self._refresh_file_lists()
        self._refresh_diff_pane()
        self.status_bar.showMessage(
            f"Committed successfully: {output.splitlines()[0] if output else 'OK'}"
        )

    def _on_push(self) -> None:
        try:
            output = self.session.push()
        except VCSError as exc:
            QMessageBox.warning(self, "Push Failed", str(exc))
            return
        self.status_bar.showMessage(
            f"Pushed successfully: {output.splitlines()[0] if output else 'OK'}"
        )

    def _run_external_tool(self, tool: ToolSpec) -> None:
        argv = self.session.build_tool_argv(tool)
        if not argv:
            return

        cwd = self.session.vcs.repo_root.resolve()
        os.chdir(cwd)
        if tool.mode == "output":
            try:
                res = run_tool_output(tool, argv, cwd)
            except FileNotFoundError as exc:
                QMessageBox.warning(
                    self, "Tool Error", f"Executable not found: {exc}"
                )
                return
            dlg = ToolOutputDialog(res, self)
            dlg.exec()
            if tool.refresh_after:
                self._on_rescan()
            return

        full_argv = argv
        if tool.mode == "terminal":
            wrapped = wrap_argv_with_cd(argv, cwd)
            launcher = find_terminal_Launcher(cwd)
            full_argv = [*launcher, *wrapped] if launcher else wrapped

        qproc = QProcess(self)
        qproc.setWorkingDirectory(str(cwd))
        qproc.setProgram(full_argv[0])
        qproc.setArguments(full_argv[1:])

        def _on_finished(_code: int, _status: QProcess.ExitStatus) -> None:
            if qproc in self._bg_processes:
                self._bg_processes.remove(qproc)
            if tool.refresh_after:
                if self.session.current_entry is not None:
                    self.session.refresh_single_file(
                        self.session.current_entry.path
                    )
                    self._refresh_file_lists()
                    self._refresh_diff_pane()
                else:
                    self._on_rescan()

        qproc.finished.connect(_on_finished)
        self._bg_processes.append(qproc)
        qproc.start()

    def _safe_vcs_call(self, action) -> None:
        try:
            action()
        except VCSError as exc:
            QMessageBox.warning(self, "VCS Error", str(exc))
            return
        self._refresh_file_lists()
        self._refresh_diff_pane()


def run_qt_app(session: ReviewSession) -> int:
    """Launch the PyQt6 GUI frontend."""
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(session)
    win.show()
    return app.exec()
