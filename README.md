# reviewtool

Local pre-commit code review and staging tool for **Git** and **Subversion (SVN)** repositories, combining a **git-gui** style two-pane staging/commit workflow with **GitLab MR** style per-file `Viewed` tracking, configurable inline diff context, and external tool launching.

Includes both a **PyQt6 Desktop GUI** and a **Textual TUI** powered by a shared headless core.

---

## Features

- **Dual Frontends**:
  - **PyQt6 GUI** (`--gui`, default when `DISPLAY` or `WAYLAND_DISPLAY` is active). Automatically saves and restores window position, size, maximization state, and splitter ratios.
  - **Textual TUI** (`--tui`, or automatic fallback in headless terminals).
- **Git & SVN Support**:
  - Auto-detects Git and SVN working copies from the current or specified directory.
  - Two-list layout (**Unstaged Changes** and **Staged Changes**) with file-level stage/unstage/revert, plus hunk-level and selected-line staging/unstaging/reverting.
  - For SVN, moving files to **Staged Changes** selects them for `svn commit` (and runs `svn add` / `svn rm` for unversioned `?` or missing `!` files).
  - Commit panel with multi-line editor, **Sign Off**, **Amend Last Commit** (Git), and **Rescan**.
- **GitLab MR Style Self-Review**:
  - Per-file **Viewed** `[x]` checkboxes in memory for the current session, plus a **Hide Viewed** filter toggle.
  - Never blocks committing if you want to commit before checking every file.
- **Large-Repo Scalability**:
  - Full repository status scans only run on startup, explicit **Rescan** (`F5`), after **Commit**, or after an external tool with `refresh_after = true`.
  - Clicking files or toggling **Viewed** never rescans the repository; diffs are loaded lazily and cached in memory.
  - Staging, unstaging, or reverting a file/hunk refreshes status for that single file path only.
- **Configurable Inline Diff, Fonts & Color Schemes**:
  - Live `+/-` context lines stepper (`[` / `]` hotkeys), **Full File** toggle, and clickable **Expand N hidden lines** dividers between hunks.
  - Configurable font sizes (`file_list_font_size`, `diff_font_size`, `commit_font_size`) with live toolbar controls for **Files Font** and **Diff Font** in the PyQt6 GUI, saved immediately to `config.ini`.
  - Configurable diff color schemes: switchable presets (`dark`, `light`, `high_contrast`) in the UI plus full custom hex overrides in the `[colors]` section of `config.ini` (`bg`, `fg`, `gutter_fg`, `hunk_fg`, `hunk_bg`, `divider_fg`, `divider_bg`, `add_bg`, `add_word_bg`, `add_fg`, `del_bg`, `del_word_bg`, `del_fg`).
  - `Ctrl+C` copies selected diff text cleanly (automatically stripping gutter line numbers).
  - Three live-switchable rendering modes:
    - `full`: Dual line-number gutter (`old | new`), green/red backgrounds, intra-line word diff highlights, and Pygments syntax highlighting.
    - `word_diff`: Dual line-number gutter, green/red backgrounds, and intra-line word diff highlights.
    - `simple`: Single line-number gutter and basic `+`/`-` colors.
- **External Tool Launcher**:
  - Launch any number of external tools (editors, IDEs, `git grep`, `blame`, linters) with the current file, line number, column, and word/selection under the cursor.

---

## Quick Start (Bootstrap)

```bash
# Create env/ virtualenv and install reviewtool + pytest
make bootstrap

# Run tests
make test

# Launch reviewtool (auto-selects PyQt6 GUI or Textual TUI)
make run

# Or explicitly pick GUI or TUI:
make run-gui
make run-tui
```

You can also invoke the binary directly from `env/bin/reviewtool`:

```bash
env/bin/reviewtool --help
env/bin/reviewtool /path/to/repo
env/bin/reviewtool --tui /path/to/repo
```

---

## Configuration (`config.ini` and `tools.ini`)

Configuration is split into two files in `~/.config/reviewtool/` (auto-created with defaults on first run):

1. **`~/.config/reviewtool/config.ini`** — Generic options and PyQt window/splitter geometry. Any option changed in the UI is saved immediately.
2. **`~/.config/reviewtool/tools.ini`** — External tool definitions (`[tool:<id>]` sections). Never overwritten when UI options are saved.

### Example `~/.config/reviewtool/tools.ini`

```ini
[tool:editor]
name = Open in Editor
command = ${EDITOR:-vim} +{line} {abs_file}
mode = terminal
shortcut = e
refresh_after = true

[tool:vscode]
name = VS Code at Line
command = code --goto {abs_file}:{line}:{col}
mode = background
shortcut = v
refresh_after = false

[tool:grep_word]
name = Grep Word in Repo
command = git grep -n {word}
mode = output
shortcut = g
refresh_after = false

[tool:blame]
name = Blame Line
command = {vcs} blame -L {line},{line} {file}
mode = output
shortcut = b
refresh_after = false
```

### Tool Execution Modes (`mode`)
- `background`: Spawns detached process in the background (ideal for GUI editors like VS Code, PyCharm, Meld).
- `terminal`: Suspends the TUI (or opens a terminal emulator from the GUI) to run interactive CLI tools like `vim` or `nano`.
- `output`: Captures `stdout`, `stderr`, and exit code and displays them in a modal output window.

### Supported Tool Command Placeholders

| Placeholder | Description |
| :--- | :--- |
| `{file}` | File path relative to the repository root |
| `{abs_file}` | Absolute filesystem path to the file |
| `{dir}` / `{abs_dir}` | Relative / absolute directory containing the file |
| `{filename}` / `{stem}` / `{ext}` | Basename, filename without extension, and extension |
| `{line}` | Target line number under cursor (new line on added/context, old line on deleted) |
| `{old_line}` / `{new_line}` | Old / new line numbers (falls back to `{line}`) |
| `{col}` | 1-based column number under cursor |
| `{word}` | Identifier word (`[A-Za-z0-9_]+`) under cursor |
| `{selection}` | Selected text in the diff view (falls back to `{word}` if empty) |
| `{line_text}` | Full text of the current diff line |
| `{repo_root}` | Absolute path to the repository root |
| `{vcs}` | `"git"` or `"svn"` |
| `{branch}` | Current Git branch name or SVN URL/revision |
| `{status}` | File status code (`M`, `A`, `D`, `?`, `!`, `R`) |
| `{staged}` | `"1"` if viewing a staged diff, `"0"` if unstaged |

---

## Keyboard Shortcuts

| Key | Action (PyQt6 GUI & Textual TUI) |
| :--- | :--- |
| `F5` | Rescan repository status |
| `n` / `p` | Move to Next / Previous Unstaged file (also `Alt+Down`/`Alt+Up` in GUI) |
| `[` / `]` | Decrease / increase diff context lines |
| `Enter` | Expand hidden lines on a gap divider |
| `s` | Stage file or hunk/selected lines (TUI) |
| `u` | Unstage file or hunk/selected lines (TUI) |
| `r` | Revert file or hunk/selected lines (TUI) |
| `v` / `Space` | Toggle file `Viewed` checkbox (or select diff line in TUI diff pane) |
| `h` | Toggle `Hide Viewed` filter (TUI) |
| `f` | Toggle `Full File` diff context (TUI) |
| `m` | Cycle diff rendering mode: `full` -> `word_diff` -> `simple` (TUI) |
| `<` / `>` (or drag `│`) | Narrow / widen TUI file lists pane (persisted immediately) |
| `z` | Toggle full-screen zoom on the TUI diff pane (hide/show left pane) |
| `o` | Open Options modal (TUI) |
| `t` | Open External Tools launcher menu (TUI) |
| `<tool shortcut>` | Run tool with matching `shortcut = ...` from `tools.ini` |
