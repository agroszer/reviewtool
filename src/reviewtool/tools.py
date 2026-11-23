"""External tool placeholder interpolation and execution engine."""

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from reviewtool.models import CursorContext, ToolSpec

_ENV_FALLBACK_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_TERMINAL_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("x-terminal-emulator", "-e"),
    ("kitty", "--directory", "{cwd}", "--"),
    ("alacritty", "--working-directory", "{cwd}", "-e"),
    ("wezterm", "start", "--cwd", "{cwd}", "--"),
    ("gnome-terminal", "--working-directory={cwd}", "--"),
    ("konsole", "--workdir", "{cwd}", "-e"),
    ("xfce4-terminal", "--working-directory={cwd}", "-x"),
    ("xterm", "-e"),
)


@dataclass(frozen=True)
class ToolRunResult:
    """Result of executing an external tool."""

    tool: ToolSpec
    argv: list[str]
    returncode: int | None
    stdout: str
    stderr: str


def build_placeholder_map(
    ctx: CursorContext,
    repo_root: Path,
    vcs_name: str,
    branch: str,
) -> dict[str, str]:
    """Build the dictionary of all 18 supported placeholders for tool commands."""
    rel_file = ctx.file
    abs_file = ctx.abs_file or (
        str((repo_root / rel_file).resolve()) if rel_file else str(repo_root)
    )
    rel_path_obj = Path(rel_file) if rel_file else Path(".")
    abs_path_obj = Path(abs_file)

    rel_dir = rel_path_obj.parent.as_posix() if rel_file else "."
    abs_dir = str(abs_path_obj.parent) if rel_file else str(repo_root)
    filename = rel_path_obj.name if rel_file else ""
    stem = rel_path_obj.stem if rel_file else ""
    ext = rel_path_obj.suffix.lstrip(".") if rel_file else ""

    line_str = str(max(1, ctx.line))
    old_line_str = str(ctx.old_line) if ctx.old_line is not None else line_str
    new_line_str = str(ctx.new_line) if ctx.new_line is not None else line_str
    col_str = str(max(1, ctx.col))
    word_str = ctx.word
    selection_str = ctx.selection if ctx.selection else word_str

    return {
        "file": rel_file,
        "abs_file": abs_file,
        "dir": rel_dir,
        "abs_dir": abs_dir,
        "filename": filename,
        "stem": stem,
        "ext": ext,
        "line": line_str,
        "old_line": old_line_str,
        "new_line": new_line_str,
        "col": col_str,
        "word": word_str,
        "selection": selection_str,
        "line_text": ctx.line_text,
        "repo_root": str(repo_root),
        "vcs": vcs_name,
        "branch": branch,
        "status": ctx.status,
        "staged": "1" if ctx.staged else "0",
    }


def _expand_env_vars(token: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_val = match.group(2) or ""
        return os.environ.get(var_name) or default_val

    expanded = _ENV_FALLBACK_RE.sub(_repl, token)
    return os.path.expandvars(expanded)


def expand_tool_command(
    command_template: str,
    placeholders: dict[str, str],
) -> list[str]:
    """Parse `command_template` into an argv list and expand all placeholders."""
    raw_tokens = shlex.split(command_template)
    argv: list[str] = []
    for tok in raw_tokens:
        env_expanded = _expand_env_vars(tok)
        out = env_expanded
        for key, val in placeholders.items():
            out = out.replace(f"{{{key}}}", val)
        if env_expanded != tok and " " in out and not tok.startswith(("'", '"')):
            argv.extend(shlex.split(out))
        else:
            argv.append(out)
    return argv


def find_terminal_Launcher(cwd: Path | None = None) -> list[str] | None:
    """Locate an installed terminal emulator prefix for GUI terminal mode."""
    cwd_str = str(cwd.resolve()) if cwd is not None else os.getcwd()
    env_term = os.environ.get("TERMINAL", "").strip()
    if env_term and shutil.which(env_term):
        return [env_term, "-e"]
    for candidate in _TERMINAL_CANDIDATES:
        if shutil.which(candidate[0]):
            return [tok.replace("{cwd}", cwd_str) for tok in candidate]
    return None


def wrap_argv_with_cd(argv: list[str], cwd: Path) -> list[str]:
    """Wrap `argv` in `sh -c 'cd "$1" && shift && exec "$@"'` for terminal tools."""
    return [
        "sh",
        "-c",
        'cd "$1" && shift && exec "$@"',
        "reviewtool-sh",
        str(cwd.resolve()),
        *argv,
    ]


def run_tool_background(
    tool: ToolSpec,
    argv: list[str],
    cwd: Path,
) -> subprocess.Popen[bytes]:
    """Spawn a background tool process (`mode = background`) in `cwd`."""
    target_cwd = cwd.resolve()
    os.chdir(target_cwd)
    return subprocess.Popen(
        argv,
        cwd=target_cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_tool_output(
    tool: ToolSpec,
    argv: list[str],
    cwd: Path,
) -> ToolRunResult:
    """Execute a tool synchronously in `cwd` and capture stdout/stderr."""
    target_cwd = cwd.resolve()
    os.chdir(target_cwd)
    proc = subprocess.run(
        argv,
        cwd=target_cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return ToolRunResult(
        tool=tool,
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def run_tool_terminal_blocking(
    tool: ToolSpec,
    argv: list[str],
    cwd: Path,
) -> int:
    """Execute an interactive terminal tool directly in the current TTY in `cwd`."""
    target_cwd = cwd.resolve()
    os.chdir(target_cwd)
    proc = subprocess.run(argv, cwd=target_cwd, check=False)
    return proc.returncode


def run_tool_in_gui_terminal(
    tool: ToolSpec,
    argv: list[str],
    cwd: Path,
) -> subprocess.Popen[bytes]:
    """Launch a terminal tool inside a desktop terminal emulator window in `cwd`."""
    target_cwd = cwd.resolve()
    os.chdir(target_cwd)
    launcher = find_terminal_Launcher(target_cwd)
    wrapped = wrap_argv_with_cd(argv, target_cwd)
    full_argv = [*launcher, *wrapped] if launcher else wrapped
    return subprocess.Popen(
        full_argv,
        cwd=target_cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


_TUI_KEY_ALIASES: dict[str, str] = {
    "return": "enter",
    "esc": "escape",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "page_up": "pageup",
    "page_down": "pagedown",
    " ": "space",
}


def normalize_textual_shortcut(shortcut: str) -> str:
    """Normalize an INI shortcut (e.g. `Ctrl+Shift+E`, `Alt+G`) to Textual syntax."""
    raw = shortcut.strip()
    if not raw:
        return ""
    parts = [p.strip().lower() for p in raw.split("+") if p.strip()]
    if not parts:
        return ""

    mods: list[str] = []
    key = parts[-1]
    for mod in parts[:-1]:
        if mod in ("ctrl", "control", "ctl"):
            mods.append("ctrl")
        elif mod in ("alt", "meta", "opt", "option"):
            mods.append("alt")
        elif mod == "shift":
            mods.append("shift")
        else:
            mods.append(mod)

    key = _TUI_KEY_ALIASES.get(key, key)
    if "shift" in mods and len(key) == 1 and key.isalpha():
        mods.remove("shift")
        key = key.upper()

    if not mods:
        return key
    return "+".join([*mods, key])

