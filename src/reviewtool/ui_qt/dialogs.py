"""PyQt6 dialogs for options configuration, tool output, and confirmations."""

from dataclasses import asdict
import shlex
from typing import Callable, cast

from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from reviewtool.config import COLOR_SCHEME_PRESETS, HOTKEY_FIELDS
from reviewtool.models import RenderMode
from reviewtool.state import ReviewSession
from reviewtool.tools import ToolRunResult


class CommandOutputDialog(QDialog):
    """Modal dialog displaying a running command and its output/error when done."""

    def __init__(
        self,
        cmd_str: str,
        output: str = "",
        is_error: bool = False,
        running: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cmd_str = cmd_str
        self._running = running
        self.setWindowTitle("Running command")
        self.resize(760, 460)

        layout = QVBoxLayout(self)
        self.header = QLabel("<b>Running command</b>", self)
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(14)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        mono = QFont("Monospace", 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(mono)
        layout.addWidget(self.editor)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, self
        )
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        layout.addWidget(self.buttons)

        if running:
            self.editor.setPlainText(f"$ {cmd_str}\n\nRunning...")
            self.buttons.setEnabled(False)
        else:
            self.set_result(output, is_error)

    def set_result(self, output: str, is_error: bool = False) -> None:
        self._running = False
        self.progress.setVisible(False)
        status_text = "Failed" if is_error else "Succeeded"
        self.setWindowTitle(f"Running command — {status_text}")
        self.header.setText(f"<b>Running command — {status_text}</b>")
        body = output.strip() or "(no output)"
        self.editor.setPlainText(f"$ {self.cmd_str}\n\n{body}")
        self.buttons.setEnabled(True)

    def reject(self) -> None:
        if not self._running:
            super().reject()


class ToolOutputDialog(CommandOutputDialog):
    """Modal dialog displaying stdout, stderr, and exit code of an external tool."""

    def __init__(
        self,
        result: ToolRunResult,
        parent: QWidget | None = None,
    ) -> None:
        cmd_str = shlex.join(result.argv)
        super().__init__(
            cmd_str=cmd_str,
            output=self.format_result(result),
            is_error=bool(result.returncode),
            running=False,
            parent=parent,
        )

    @staticmethod
    def format_result(result: ToolRunResult) -> str:
        combined: list[str] = [
            f"Tool: {result.tool.name} | Exit Code: {result.returncode}"
        ]
        if result.stdout:
            combined.append(result.stdout.rstrip("\n"))
        if result.stderr:
            combined.append("--- stderr ---")
            combined.append(result.stderr.rstrip("\n"))
        if not result.stdout and not result.stderr:
            combined.append("(no output)")
        return "\n".join(combined)


_COLOR_FIELDS: tuple[tuple[str, str], ...] = (
    ("bg", "Background"),
    ("fg", "Foreground"),
    ("gutter_fg", "Gutter Line Numbers"),
    ("hunk_fg", "Hunk Header Text"),
    ("hunk_bg", "Hunk Header Background"),
    ("divider_fg", "Expand Divider Text"),
    ("divider_bg", "Expand Divider Background"),
    ("add_bg", "Added Line Background"),
    ("add_word_bg", "Added Word Highlight"),
    ("add_fg", "Added Line Text (Simple)"),
    ("del_bg", "Deleted Line Background"),
    ("del_word_bg", "Deleted Word Highlight"),
    ("del_fg", "Deleted Line Text (Simple)"),
)


class OptionsDialog(QDialog):
    """Modal dialog for configuring display, font sizes, and diff color scheme.

    All changes are applied live and saved to config.ini immediately.
    """

    def __init__(
        self,
        session: ReviewSession,
        on_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.on_changed = on_changed
        self._syncing = True
        self._color_edits: dict[str, QLineEdit] = {}

        self.setWindowTitle("Options")
        self.resize(520, 620)
        self._build_ui()
        self._syncing = False

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget(self)
        root.addWidget(tabs)

        general_tab = QWidget(self)
        gen_layout = QVBoxLayout(general_tab)
        gen = self.session.config.general

        display_group = QGroupBox("Diff & View Options", general_tab)
        d_form = QFormLayout(display_group)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem(
            "Full (Dual Gutter + Word Diff + Syntax)", "full"
        )
        self.combo_mode.addItem(
            "Word Diff (Dual Gutter + Word Diff)", "word_diff"
        )
        self.combo_mode.addItem(
            "Simple (Single Gutter + Basic Colors)", "simple"
        )
        m_idx = self.combo_mode.findData(self.session.render_mode)
        if m_idx >= 0:
            self.combo_mode.setCurrentIndex(m_idx)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        d_form.addRow("Render Mode:", self.combo_mode)

        self.spin_tab = QSpinBox()
        self.spin_tab.setRange(1, 16)
        self.spin_tab.setValue(gen.tab_width)
        self.spin_tab.valueChanged.connect(self._on_tab_changed)
        d_form.addRow("Tab Width:", self.spin_tab)

        self.chk_hide_viewed = QCheckBox("Hide files marked as Viewed")
        self.chk_hide_viewed.setChecked(self.session.hide_viewed)
        self.chk_hide_viewed.toggled.connect(self._on_hide_viewed_changed)
        d_form.addRow("File List Filter:", self.chk_hide_viewed)
        gen_layout.addWidget(display_group)

        font_group = QGroupBox("Fonts & Sizes", general_tab)
        f_form = QFormLayout(font_group)

        self.font_combo_diff = QFontComboBox()
        self.font_combo_diff.setCurrentFont(QFont(gen.diff_font_family))
        self.font_combo_diff.currentFontChanged.connect(
            self._on_diff_font_family_changed
        )
        f_form.addRow("Diff View Font:", self.font_combo_diff)

        self.spin_diff_font = QSpinBox()
        self.spin_diff_font.setRange(6, 36)
        self.spin_diff_font.setValue(gen.diff_font_size)
        self.spin_diff_font.valueChanged.connect(self._on_diff_font_changed)
        f_form.addRow("Diff View Font Size:", self.spin_diff_font)

        self.spin_files_font = QSpinBox()
        self.spin_files_font.setRange(6, 36)
        self.spin_files_font.setValue(gen.file_list_font_size)
        self.spin_files_font.valueChanged.connect(self._on_files_font_changed)
        f_form.addRow("File Lists Font Size:", self.spin_files_font)

        self.spin_commit_font = QSpinBox()
        self.spin_commit_font.setRange(6, 36)
        self.spin_commit_font.setValue(gen.commit_font_size)
        self.spin_commit_font.valueChanged.connect(self._on_commit_font_changed)
        f_form.addRow("Commit Box Font Size:", self.spin_commit_font)
        gen_layout.addWidget(font_group)

        color_group = QGroupBox("Diff Color Scheme", general_tab)
        c_form = QFormLayout(color_group)

        self.combo_theme = QComboBox()
        for preset_id, preset_label, _scheme in COLOR_SCHEME_PRESETS:
            self.combo_theme.addItem(preset_label, preset_id)
        cur_theme = gen.theme.lower()
        if cur_theme == "dark":
            cur_theme = "vscode_dark"
        elif cur_theme == "light":
            cur_theme = "vscode_light"
        t_idx = self.combo_theme.findData(cur_theme)
        if t_idx >= 0:
            self.combo_theme.setCurrentIndex(t_idx)
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        c_form.addRow("Color Preset:", self.combo_theme)

        for field_key, label_text in _COLOR_FIELDS:
            row_w = QWidget(general_tab)
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(self._get_color_val(field_key))
            edit.editingFinished.connect(
                self._make_color_edit_cb(field_key, edit)
            )
            btn_pick = QPushButton("Pick...")
            btn_pick.setFixedWidth(70)
            btn_pick.clicked.connect(
                self._make_color_pick_cb(field_key, edit)
            )
            row_l.addWidget(edit)
            row_l.addWidget(btn_pick)
            self._color_edits[field_key] = edit
            c_form.addRow(f"{label_text}:", row_w)

        gen_layout.addWidget(color_group)
        tabs.addTab(general_tab, "General")

        keys_tab = QWidget(self)
        keys_layout = QVBoxLayout(keys_tab)
        hotkey_group = QGroupBox(
            "Button Hotkeys (comma-separated for multiple)", keys_tab
        )
        h_form = QFormLayout(hotkey_group)
        hk_dict = asdict(self.session.config.hotkeys)
        for hk_key, hk_label in HOTKEY_FIELDS:
            hk_edit = QLineEdit(hk_dict[hk_key])
            hk_edit.textChanged.connect(
                self._make_hotkey_edit_cb(hk_key, hk_edit)
            )
            h_form.addRow(f"{hk_label}:", hk_edit)
        keys_layout.addWidget(hotkey_group)
        keys_layout.addStretch(1)
        tabs.addTab(keys_tab, "Keys")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, self
        )
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _get_color_val(self, key: str) -> str:
        c = self.session.config.colors
        mapping = {
            "bg": c.bg,
            "fg": c.fg,
            "gutter_fg": c.gutter_fg,
            "hunk_fg": c.hunk_fg,
            "hunk_bg": c.hunk_bg,
            "divider_fg": c.divider_fg,
            "divider_bg": c.divider_bg,
            "add_bg": c.add_bg,
            "add_word_bg": c.add_word_bg,
            "add_fg": c.add_fg,
            "del_bg": c.del_bg,
            "del_word_bg": c.del_word_bg,
            "del_fg": c.del_fg,
        }
        return mapping[key]

    def _set_color_val(self, key: str, value: str) -> None:
        val = value.strip()
        if not val:
            return
        c = self.session.config.colors
        if key == "bg":
            c.bg = val
        elif key == "fg":
            c.fg = val
        elif key == "gutter_fg":
            c.gutter_fg = val
        elif key == "hunk_fg":
            c.hunk_fg = val
        elif key == "hunk_bg":
            c.hunk_bg = val
        elif key == "divider_fg":
            c.divider_fg = val
        elif key == "divider_bg":
            c.divider_bg = val
        elif key == "add_bg":
            c.add_bg = val
        elif key == "add_word_bg":
            c.add_word_bg = val
        elif key == "add_fg":
            c.add_fg = val
        elif key == "del_bg":
            c.del_bg = val
        elif key == "del_word_bg":
            c.del_word_bg = val
        elif key == "del_fg":
            c.del_fg = val
        self.session.config.save_general()
        self.on_changed()

    def _make_color_edit_cb(
        self, key: str, edit: QLineEdit
    ) -> Callable[[], None]:
        def _cb() -> None:
            if not self._syncing:
                self._set_color_val(key, edit.text())

        return _cb

    def _make_hotkey_edit_cb(
        self, key: str, edit: QLineEdit
    ) -> Callable[..., None]:
        def _cb(*_args: object) -> None:
            if not self._syncing:
                setattr(
                    self.session.config.hotkeys, key, edit.text().strip()
                )
                self.session.config.save_general()
                self.on_changed()

        return _cb

    def _make_color_pick_cb(
        self, key: str, edit: QLineEdit
    ) -> Callable[[], None]:
        def _cb() -> None:
            chosen = QColorDialog.getColor(
                QColor(edit.text()), self, f"Select {key}"
            )
            if chosen.isValid():
                hex_str = chosen.name()
                edit.setText(hex_str)
                self._set_color_val(key, hex_str)

        return _cb

    def _on_mode_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        mode = cast(RenderMode, self.combo_mode.currentData())
        self.session.set_render_mode(mode)
        self.on_changed()

    def _on_tab_changed(self, val: int) -> None:
        if self._syncing:
            return
        self.session.config.general.tab_width = val
        self.session.config.save_general()
        self.on_changed()

    def _on_hide_viewed_changed(self, checked: bool) -> None:
        if self._syncing:
            return
        self.session.set_hide_viewed(checked)
        self.on_changed()

    def _on_files_font_changed(self, size: int) -> None:
        if self._syncing:
            return
        self.session.config.set_file_list_font_size(size)
        self.on_changed()

    def _on_diff_font_family_changed(self, font: QFont) -> None:
        if self._syncing:
            return
        self.session.config.set_diff_font_family(font.family())
        self.on_changed()

    def _on_diff_font_changed(self, size: int) -> None:
        if self._syncing:
            return
        self.session.config.set_diff_font_size(size)
        self.on_changed()

    def _on_commit_font_changed(self, size: int) -> None:
        if self._syncing:
            return
        self.session.config.set_commit_font_size(size)
        self.on_changed()

    def _on_theme_changed(self, _idx: int) -> None:
        if self._syncing:
            return
        theme = str(self.combo_theme.currentData() or "dark")
        self.session.config.set_theme(theme)
        self._syncing = True
        for field_key, edit in self._color_edits.items():
            edit.setText(self._get_color_val(field_key))
        self._syncing = False
        self.on_changed()


def confirm_action(
    parent: QWidget | None,
    title: str,
    message: str,
) -> bool:
    """Ask a Yes/No confirmation question in PyQt6."""
    ans = QMessageBox.question(
        parent,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return ans == QMessageBox.StandardButton.Yes
