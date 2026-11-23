"""Unified diff parser, intra-line word diff calculator, and partial patch builder."""

import difflib
import re
from pathlib import Path

from reviewtool.models import DiffHunk, DiffLine, ExpandDivider, FileDiff

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$"
)
_WORD_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]+")
_IDENT_RE = re.compile(r"[A-Za-z0-9_]+")


def parse_unified_diff(raw_diff: str, path: str) -> FileDiff:
    """Parse unified diff output from git or svn into a FileDiff."""
    file_diff = FileDiff(path=path)
    if not raw_diff:
        return file_diff

    lines = raw_diff.splitlines()
    current_hunk: DiffHunk | None = None
    old_ln = 0
    new_ln = 0

    for line in lines:
        if line.startswith("Binary files ") or line.startswith(
            "Cannot display: file marked as a binary type."
        ):
            file_diff.is_binary = True
            continue

        if current_hunk is None:
            if line.startswith("--- "):
                file_diff.old_header = line
                continue
            if line.startswith("+++ "):
                file_diff.new_header = line
                continue
            match = _HUNK_HEADER_RE.match(line)
            if match:
                current_hunk, old_ln, new_ln = _make_hunk_from_match(match)
                file_diff.hunks.append(current_hunk)
            else:
                file_diff.preamble.append(line)
            continue

        match = _HUNK_HEADER_RE.match(line)
        if match:
            current_hunk, old_ln, new_ln = _make_hunk_from_match(match)
            file_diff.hunks.append(current_hunk)
            continue

        if line.startswith("+"):
            current_hunk.lines.append(
                DiffLine(kind="add", text=line[1:], old_lineno=None, new_lineno=new_ln)
            )
            new_ln += 1
        elif line.startswith("-"):
            current_hunk.lines.append(
                DiffLine(kind="del", text=line[1:], old_lineno=old_ln, new_lineno=None)
            )
            old_ln += 1
        elif line.startswith(" "):
            current_hunk.lines.append(
                DiffLine(
                    kind="context",
                    text=line[1:],
                    old_lineno=old_ln,
                    new_lineno=new_ln,
                )
            )
            old_ln += 1
            new_ln += 1
        elif line.startswith("\\"):
            current_hunk.lines.append(
                DiffLine(kind="meta", text=line, old_lineno=None, new_lineno=None)
            )
        else:
            current_hunk.lines.append(
                DiffLine(
                    kind="context",
                    text=line,
                    old_lineno=old_ln,
                    new_lineno=new_ln,
                )
            )
            old_ln += 1
            new_ln += 1

    for hunk in file_diff.hunks:
        compute_word_diffs(hunk)

    return file_diff


def _make_hunk_from_match(match: re.Match[str]) -> tuple[DiffHunk, int, int]:
    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    header_ctx = match.group(5) or ""
    hunk = DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        header_context=header_ctx,
    )
    return hunk, old_start, new_start


def synthesize_untracked_diff(file_path: Path, rel_path: str) -> FileDiff:
    """Create a synthetic all-added FileDiff for an untracked file."""
    file_diff = FileDiff(
        path=rel_path,
        old_header="--- /dev/null",
        new_header=f"+++ b/{rel_path}",
        is_untracked=True,
    )
    if not file_path.exists() or file_path.is_dir():
        return file_diff

    raw_bytes = file_path.read_bytes()
    if b"\x00" in raw_bytes[:8192]:
        file_diff.is_binary = True
        return file_diff

    content = raw_bytes.decode("utf-8", errors="replace")
    text_lines = content.splitlines()
    file_diff.total_new_lines = len(text_lines)
    if not text_lines:
        return file_diff

    hunk = DiffHunk(
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=len(text_lines),
    )
    for idx, line in enumerate(text_lines, start=1):
        hunk.lines.append(
            DiffLine(kind="add", text=line, old_lineno=None, new_lineno=idx)
        )
    file_diff.hunks.append(hunk)
    return file_diff


def compute_word_diffs(hunk: DiffHunk) -> None:
    """Populate intra-line word highlight spans for paired del/add blocks."""
    idx = 0
    total = len(hunk.lines)
    while idx < total:
        if hunk.lines[idx].kind != "del":
            idx += 1
            continue
        del_start = idx
        while idx < total and hunk.lines[idx].kind == "del":
            idx += 1
        del_end = idx

        add_start = idx
        while idx < total and hunk.lines[idx].kind == "add":
            idx += 1
        add_end = idx

        del_block = hunk.lines[del_start:del_end]
        add_block = hunk.lines[add_start:add_end]
        if len(del_block) == len(add_block) and len(del_block) <= 25:
            for d_line, a_line in zip(del_block, add_block):
                d_spans, a_spans = _diff_line_pair(d_line.text, a_line.text)
                d_line.word_spans = d_spans
                a_line.word_spans = a_spans


def _diff_line_pair(
    old_text: str, new_text: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    old_tokens = _WORD_TOKEN_RE.findall(old_text)
    new_tokens = _WORD_TOKEN_RE.findall(new_text)

    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)
    old_spans: list[tuple[int, int]] = []
    new_spans: list[tuple[int, int]] = []

    old_offsets = _token_offsets(old_tokens)
    new_offsets = _token_offsets(new_tokens)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 < i2:
            start_char = old_offsets[i1][0]
            end_char = old_offsets[i2 - 1][1]
            old_spans.append((start_char, end_char))
        if j1 < j2:
            start_char = new_offsets[j1][0]
            end_char = new_offsets[j2 - 1][1]
            new_spans.append((start_char, end_char))

    return old_spans, new_spans


def _token_offsets(tokens: list[str]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    pos = 0
    for tok in tokens:
        end = pos + len(tok)
        offsets.append((pos, end))
        pos = end
    return offsets


def compute_expand_dividers(file_diff: FileDiff) -> list[ExpandDivider]:
    """Return ExpandDivider markers for hidden line gaps before/between/after hunks."""
    dividers: list[ExpandDivider] = []
    if file_diff.is_binary or not file_diff.hunks:
        return dividers

    prev_old_end = 0
    prev_new_end = 0
    for idx, hunk in enumerate(file_diff.hunks):
        if hunk.new_start > prev_new_end + 1 and hunk.old_start > prev_old_end + 1:
            dividers.append(
                ExpandDivider(
                    before_hunk_index=idx,
                    old_start=prev_old_end + 1,
                    old_end=hunk.old_start - 1,
                    new_start=prev_new_end + 1,
                    new_end=hunk.new_start - 1,
                )
            )
        prev_old_end = hunk.old_start + max(0, hunk.old_count - 1)
        prev_new_end = hunk.new_start + max(0, hunk.new_count - 1)

    if (
        file_diff.total_new_lines is not None
        and file_diff.total_new_lines > prev_new_end
    ):
        trailing_count = file_diff.total_new_lines - prev_new_end
        dividers.append(
            ExpandDivider(
                before_hunk_index=len(file_diff.hunks),
                old_start=prev_old_end + 1,
                old_end=prev_old_end + trailing_count,
                new_start=prev_new_end + 1,
                new_end=file_diff.total_new_lines,
            )
        )
    return dividers


def expand_divider_in_diff(
    file_diff: FileDiff,
    divider: ExpandDivider,
    working_lines: list[str],
    step: int | None = None,
) -> None:
    """Expand a hidden gap between hunks using lines from the working file."""
    if divider.new_start < 1 or divider.new_start > len(working_lines):
        return
    actual_new_end = min(divider.new_end, len(working_lines))
    span = actual_new_end - divider.new_start + 1
    if step is not None and step > 0 and span > step:
        actual_new_end = divider.new_start + step - 1

    slice_texts = working_lines[divider.new_start - 1 : actual_new_end]
    count = len(slice_texts)
    if count == 0:
        return

    expanded_lines: list[DiffLine] = []
    for offset, text in enumerate(slice_texts):
        expanded_lines.append(
            DiffLine(
                kind="context",
                text=text,
                old_lineno=divider.old_start + offset,
                new_lineno=divider.new_start + offset,
            )
        )

    full_gap = actual_new_end == min(divider.new_end, len(working_lines))
    idx = divider.before_hunk_index

    if idx == 0:
        first_hunk = file_diff.hunks[0]
        if full_gap:
            first_hunk.lines = expanded_lines + first_hunk.lines
            first_hunk.old_start = divider.old_start
            first_hunk.old_count += count
            first_hunk.new_start = divider.new_start
            first_hunk.new_count += count
        else:
            new_hunk = DiffHunk(
                old_start=divider.old_start,
                old_count=count,
                new_start=divider.new_start,
                new_count=count,
                lines=expanded_lines,
            )
            file_diff.hunks.insert(0, new_hunk)
    elif idx < len(file_diff.hunks):
        prev_hunk = file_diff.hunks[idx - 1]
        next_hunk = file_diff.hunks[idx]
        if full_gap:
            prev_hunk.lines.extend(expanded_lines)
            prev_hunk.lines.extend(next_hunk.lines)
            prev_hunk.old_count += count + next_hunk.old_count
            prev_hunk.new_count += count + next_hunk.new_count
            file_diff.hunks.pop(idx)
        else:
            prev_hunk.lines.extend(expanded_lines)
            prev_hunk.old_count += count
            prev_hunk.new_count += count
    else:
        last_hunk = file_diff.hunks[-1]
        last_hunk.lines.extend(expanded_lines)
        last_hunk.old_count += count
        last_hunk.new_count += count


def build_partial_patch(
    file_diff: FileDiff,
    hunk_index: int,
    selected_line_indices: set[int] | None = None,
    reverse: bool = False,
) -> str:
    """Build a valid unified diff patch for a single hunk or selected lines in it.

    When `reverse=False` (staging unstaged changes):
      - Unselected `del` (`-`) lines are converted to `context` (` `) lines.
      - Unselected `add` (`+`) lines are omitted.
    When `reverse=True` (unstaging staged changes or reverting unstaged changes
    where patch will be applied with `--reverse`):
      - If we pass the patch to `git apply --reverse`, we still construct a forward
        patch representing ONLY the selected changes relative to the post-image, OR
        we construct a forward patch ready for `--reverse`.
      - Specifically, for `git apply --reverse`: the target being patched has the `+`
        lines and lacks the `-` lines. So unselected `+` lines must be kept as
        `context` (` `) lines, and unselected `-` lines must be omitted!
    """
    if hunk_index < 0 or hunk_index >= len(file_diff.hunks):
        raise IndexError(f"Hunk index {hunk_index} out of range")

    hunk = file_diff.hunks[hunk_index]
    new_lines: list[str] = []
    old_count = 0
    new_count = 0
    has_changes = False

    for idx, dline in enumerate(hunk.lines):
        if dline.kind == "meta":
            new_lines.append(dline.text)
            continue

        is_selected = (
            selected_line_indices is None or idx in selected_line_indices
        )

        if dline.kind == "context":
            new_lines.append(f" {dline.text}")
            old_count += 1
            new_count += 1
        elif dline.kind == "del":
            if is_selected:
                new_lines.append(f"-{dline.text}")
                old_count += 1
                has_changes = True
            elif not reverse:
                new_lines.append(f" {dline.text}")
                old_count += 1
                new_count += 1
        elif dline.kind == "add":
            if is_selected:
                new_lines.append(f"+{dline.text}")
                new_count += 1
                has_changes = True
            elif reverse:
                new_lines.append(f" {dline.text}")
                old_count += 1
                new_count += 1

    if not has_changes:
        return ""

    old_hdr = file_diff.old_header or f"--- a/{file_diff.path}"
    new_hdr = file_diff.new_header or f"+++ b/{file_diff.path}"
    if not old_hdr.startswith("--- a/") and not old_hdr.startswith("--- /dev/null"):
        old_hdr = f"--- a/{file_diff.path}"
    if not new_hdr.startswith("+++ b/") and not new_hdr.startswith("+++ /dev/null"):
        new_hdr = f"+++ b/{file_diff.path}"

    hunk_header = (
        f"@@ -{hunk.old_start},{old_count} +{hunk.new_start},{new_count} @@"
    )
    patch_lines = [old_hdr, new_hdr, hunk_header, *new_lines]
    return "\n".join(patch_lines) + "\n"


def apply_reverse_hunk_to_lines(
    working_lines: list[str],
    hunk: DiffHunk,
    selected_line_indices: set[int] | None = None,
) -> list[str]:
    """Apply a reverse hunk/line revert directly to in-memory file lines (for SVN)."""
    result = list(working_lines)
    insert_pos = max(0, hunk.new_start - 1)
    span_len = hunk.new_count

    replacement: list[str] = []
    for idx, dline in enumerate(hunk.lines):
        if dline.kind == "meta":
            continue
        is_selected = (
            selected_line_indices is None or idx in selected_line_indices
        )
        if dline.kind == "context":
            replacement.append(dline.text)
        elif dline.kind == "del":
            if is_selected:
                replacement.append(dline.text)
        elif dline.kind == "add":
            if not is_selected:
                replacement.append(dline.text)

    result[insert_pos : insert_pos + span_len] = replacement
    return result


def extract_word_at_column(line_text: str, col_1based: int) -> str:
    """Extract the identifier (`[A-Za-z0-9_]+`) under or nearest `col_1based`."""
    if not line_text:
        return ""
    idx = max(0, min(len(line_text) - 1, col_1based - 1))
    for match in _IDENT_RE.finditer(line_text):
        start, end = match.span()
        if start <= idx < end:
            return match.group(0)
        if idx == end and end > start:
            return match.group(0)
    first_match = _IDENT_RE.search(line_text)
    return first_match.group(0) if first_match else ""
