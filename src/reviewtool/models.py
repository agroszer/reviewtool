"""Core data models for reviewtool."""

from dataclasses import dataclass, field
from typing import Literal

RenderMode = Literal["full", "word_diff", "simple"]
ToolMode = Literal["background", "terminal", "output"]
LineKind = Literal["context", "add", "del", "meta"]


@dataclass(frozen=True)
class FileEntry:
    """Represents a single changed file in the working tree or staging area."""

    path: str
    status: str
    staged: bool
    orig_path: str | None = None


@dataclass
class DiffLine:
    """A single line within a diff hunk."""

    kind: LineKind
    text: str
    old_lineno: int | None = None
    new_lineno: int | None = None
    word_spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class DiffHunk:
    """A unified diff hunk (`@@ -old_start,old_count +new_start,new_count @@`)."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header_context: str = ""
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def header(self) -> str:
        suffix = f" {self.header_context}" if self.header_context else ""
        return (
            f"@@ -{self.old_start},{self.old_count} "
            f"+{self.new_start},{self.new_count} @@{suffix}"
        )


@dataclass(frozen=True)
class ExpandDivider:
    """Represents a hidden range of unchanged lines between two hunks."""

    before_hunk_index: int
    old_start: int
    old_end: int
    new_start: int
    new_end: int

    @property
    def hidden_count(self) -> int:
        return max(0, self.new_end - self.new_start + 1)


@dataclass
class FileDiff:
    """Parsed diff for a single file."""

    path: str
    old_header: str = ""
    new_header: str = ""
    preamble: list[str] = field(default_factory=list)
    hunks: list[DiffHunk] = field(default_factory=list)
    is_binary: bool = False
    is_untracked: bool = False
    total_new_lines: int | None = None


@dataclass(frozen=True)
class ToolSpec:
    """External tool specification loaded from tools.ini."""

    key: str
    name: str
    command: str
    mode: ToolMode = "background"
    shortcut: str = ""
    refresh_after: bool = False


@dataclass
class CursorContext:
    """Current file, cursor line/column, and word/selection context for tools."""

    file: str = ""
    abs_file: str = ""
    line: int = 1
    old_line: int | None = None
    new_line: int | None = None
    col: int = 1
    word: str = ""
    selection: str = ""
    line_text: str = ""
    status: str = "M"
    staged: bool = False
