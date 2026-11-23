"""Headless review session controller with lazy diff caching and targeted updates."""

from pathlib import Path

from reviewtool.config import ConfigManager
from reviewtool.diff import expand_divider_in_diff
from reviewtool.models import (
    CursorContext,
    ExpandDivider,
    FileDiff,
    FileEntry,
    RenderMode,
    ToolSpec,
)
from reviewtool.tools import build_placeholder_map, expand_tool_command
from reviewtool.vcs.base import VCSBackend

DiffCacheKey = tuple[str, bool, int, bool]


class ReviewSession:
    """Manages file lists, in-memory Viewed checkboxes, and lazy diff cache.

    Scalability design:
    - Full repository scan (`rescan()`) only runs on startup, explicit Rescan,
      after Commit, or after a tool with `refresh_after = true`.
    - Selecting a file or toggling Viewed never scans the repository.
    - Stage/Unstage/Revert of a single file or hunk updates ONLY that path via
      `vcs.scan_file_status(path)` and invalidates only that file's cache entries.
    """

    def __init__(self, vcs: VCSBackend, config: ConfigManager) -> None:
        self.vcs = vcs
        self.config = config
        self.unstaged: list[FileEntry] = []
        self.staged: list[FileEntry] = []
        self.viewed_paths: set[str] = set()
        self._diff_cache: dict[DiffCacheKey, FileDiff] = {}
        self.current_entry: FileEntry | None = None
        self.current_diff: FileDiff | None = None
        self.cursor = CursorContext()
        self._branch_cache: str = ""

    @property
    def context_lines(self) -> int:
        return self.config.general.context_lines

    @property
    def full_file(self) -> bool:
        return self.config.general.full_file

    @property
    def render_mode(self) -> RenderMode:
        return self.config.general.render_mode

    @property
    def hide_viewed(self) -> bool:
        return self.config.general.hide_viewed

    @property
    def branch_name(self) -> str:
        if not self._branch_cache:
            self._branch_cache = self.vcs.get_branch_or_info()
        return self._branch_cache

    def rescan(self) -> None:
        """Perform an explicit full repository status scan and clear the diff cache."""
        self._diff_cache.clear()
        self._branch_cache = self.vcs.get_branch_or_info()
        self.unstaged, self.staged = self.vcs.scan_status()

        active_paths = {e.path for e in self.unstaged} | {
            e.path for e in self.staged
        }
        self.viewed_paths.intersection_update(active_paths)

        if self.current_entry is not None:
            match = self._find_entry(
                self.current_entry.path, self.current_entry.staged
            )
            if match is None:
                match = self._find_entry(
                    self.current_entry.path, not self.current_entry.staged
                )
            self.select_entry(match)
        elif self.unstaged:
            self.select_entry(self.unstaged[0])
        elif self.staged:
            self.select_entry(self.staged[0])
        else:
            self.select_entry(None)

    def refresh_single_file(
        self, rel_path: str, prefer_staged: bool | None = None
    ) -> None:
        """Update status and invalidate diff cache for a single file path only."""
        self._invalidate_file_cache(rel_path)
        u_entry, s_entry = self.vcs.scan_file_status(rel_path)

        self.unstaged = self._replace_or_remove(self.unstaged, rel_path, u_entry)
        self.staged = self._replace_or_remove(self.staged, rel_path, s_entry)

        if u_entry is None and s_entry is None:
            self.viewed_paths.discard(rel_path)

        if self.current_entry is not None and self.current_entry.path == rel_path:
            target_staged = (
                prefer_staged
                if prefer_staged is not None
                else self.current_entry.staged
            )
            if target_staged and s_entry is not None:
                self.select_entry(s_entry)
            elif not target_staged and u_entry is not None:
                self.select_entry(u_entry)
            elif u_entry is not None:
                self.select_entry(u_entry)
            elif s_entry is not None:
                self.select_entry(s_entry)
            else:
                self._select_fallback_entry()

    def _select_fallback_entry(self) -> None:
        visible_u = self.visible_unstaged()
        visible_s = self.visible_staged()
        if visible_u:
            self.select_entry(visible_u[0])
        elif visible_s:
            self.select_entry(visible_s[0])
        else:
            self.select_entry(None)

    def _replace_or_remove(
        self,
        entries: list[FileEntry],
        rel_path: str,
        new_entry: FileEntry | None,
    ) -> list[FileEntry]:
        result: list[FileEntry] = []
        replaced = False
        for item in entries:
            if item.path == rel_path:
                if new_entry is not None and not replaced:
                    result.append(new_entry)
                    replaced = True
            else:
                result.append(item)
        if new_entry is not None and not replaced:
            result.append(new_entry)
        return result

    def _invalidate_file_cache(self, rel_path: str) -> None:
        to_remove = [k for k in self._diff_cache if k[0] == rel_path]
        for key in to_remove:
            del self._diff_cache[key]

    def _find_entry(self, rel_path: str, staged: bool) -> FileEntry | None:
        pool = self.staged if staged else self.unstaged
        for entry in pool:
            if entry.path == rel_path:
                return entry
        return None

    def visible_unstaged(self) -> list[FileEntry]:
        """Return unstaged files, filtered by `hide_viewed` if active."""
        if not self.hide_viewed:
            return list(self.unstaged)
        return [e for e in self.unstaged if e.path not in self.viewed_paths]

    def visible_staged(self) -> list[FileEntry]:
        """Return staged files, filtered by `hide_viewed` if active."""
        if not self.hide_viewed:
            return list(self.staged)
        return [e for e in self.staged if e.path not in self.viewed_paths]

    def is_viewed(self, rel_path: str) -> bool:
        return rel_path in self.viewed_paths

    def select_adjacent_unstaged(self, delta: int) -> FileEntry | None:
        """Select the next (`delta=+1`) or previous (`delta=-1`) unstaged file."""
        vis = self.visible_unstaged()
        if not vis:
            return None
        cur = self.current_entry
        if cur is None or cur.staged:
            target = vis[0] if delta >= 0 else vis[-1]
        else:
            cur_idx = next(
                (i for i, e in enumerate(vis) if e.path == cur.path), -1
            )
            if cur_idx < 0:
                target = vis[0] if delta >= 0 else vis[-1]
            else:
                target = vis[(cur_idx + delta) % len(vis)]
        self.select_entry(target)
        return target

    def set_viewed(self, rel_path: str, viewed: bool) -> None:
        """Set in-memory Viewed status for a file without rescanning."""
        if viewed:
            self.viewed_paths.add(rel_path)
        else:
            self.viewed_paths.discard(rel_path)

    def toggle_viewed(self, rel_path: str) -> bool:
        """Toggle in-memory Viewed status for a file and return the new state."""
        new_state = rel_path not in self.viewed_paths
        self.set_viewed(rel_path, new_state)
        return new_state

    def select_entry(self, entry: FileEntry | None) -> FileDiff | None:
        """Select a file entry and load its diff from cache or VCS lazily."""
        self.current_entry = entry
        if entry is None:
            self.current_diff = None
            self.cursor = CursorContext()
            return None

        key: DiffCacheKey = (
            entry.path,
            entry.staged,
            self.context_lines,
            self.full_file,
        )
        cached = self._diff_cache.get(key)
        if cached is None:
            cached = self.vcs.get_diff(
                entry,
                context_lines=self.context_lines,
                full_file=self.full_file,
            )
            self._diff_cache[key] = cached

        self.current_diff = cached
        first_line = 1
        if cached.hunks and cached.hunks[0].lines:
            first_dline = cached.hunks[0].lines[0]
            first_line = (
                first_dline.new_lineno
                or first_dline.old_lineno
                or cached.hunks[0].new_start
                or 1
            )
        self.cursor = CursorContext(
            file=entry.path,
            abs_file=str((self.vcs.repo_root / entry.path).resolve()),
            line=first_line,
            old_line=None,
            new_line=first_line,
            col=1,
            word="",
            selection="",
            line_text="",
            status=entry.status,
            staged=entry.staged,
        )
        return cached

    def expand_gap(
        self, divider: ExpandDivider, step: int | None = None
    ) -> None:
        """Expand a hidden line gap between hunks in the currently displayed diff."""
        if self.current_diff is None or self.current_entry is None:
            return
        working_lines = self.vcs.get_working_file_lines(self.current_entry.path)
        expand_divider_in_diff(
            self.current_diff, divider, working_lines, step=step
        )

    def set_context_lines(self, lines: int) -> None:
        """Change base context lines, save config.ini ASAP, and reload diff."""
        self.config.set_context_lines(lines)
        if self.current_entry is not None:
            self.select_entry(self.current_entry)

    def adjust_context_lines(self, delta: int) -> None:
        """Increment or decrement context lines by `delta` and save ASAP."""
        self.set_context_lines(max(0, self.context_lines + delta))

    def set_full_file(self, full_file: bool) -> None:
        """Toggle full-file context, save config.ini ASAP, and reload diff."""
        self.config.set_full_file(full_file)
        if self.current_entry is not None:
            self.select_entry(self.current_entry)

    def set_render_mode(self, mode: RenderMode) -> None:
        """Change diff rendering mode and persist to config.ini ASAP."""
        self.config.set_render_mode(mode)

    def set_hide_viewed(self, hide_viewed: bool) -> None:
        """Toggle hiding Viewed files and persist to config.ini ASAP."""
        self.config.set_hide_viewed(hide_viewed)

    def stage_file(self, rel_path: str) -> None:
        """Stage a single file and refresh only that file's status."""
        self.vcs.stage_file(rel_path)
        self.refresh_single_file(rel_path, prefer_staged=False)

    def unstage_file(self, rel_path: str) -> None:
        """Unstage a single file and refresh only that file's status."""
        self.vcs.unstage_file(rel_path)
        self.refresh_single_file(rel_path, prefer_staged=True)

    def stage_all(self) -> None:
        """Stage all unstaged files and refresh status."""
        self.vcs.stage_all(self.unstaged)
        self.rescan()

    def unstage_all(self) -> None:
        """Unstage all staged files and refresh status."""
        self.vcs.unstage_all(self.staged)
        self.rescan()

    def revert_file(self, entry: FileEntry) -> None:
        """Revert a single file and refresh only that file's status."""
        self.vcs.revert_file(entry)
        self.refresh_single_file(entry.path)

    def stage_hunk_or_lines(
        self, hunk_index: int, selected_lines: set[int] | None = None
    ) -> None:
        """Stage a hunk or selected lines in the current unstaged diff."""
        if self.current_diff is None or self.current_entry is None:
            return
        if self.current_entry.staged:
            return
        path = self.current_entry.path
        self.vcs.stage_hunk_or_lines(
            self.current_diff, hunk_index, selected_lines
        )
        self.refresh_single_file(path, prefer_staged=False)

    def unstage_hunk_or_lines(
        self, hunk_index: int, selected_lines: set[int] | None = None
    ) -> None:
        """Unstage a hunk or selected lines in the current staged diff."""
        if self.current_diff is None or self.current_entry is None:
            return
        if not self.current_entry.staged:
            return
        path = self.current_entry.path
        self.vcs.unstage_hunk_or_lines(
            self.current_diff, hunk_index, selected_lines
        )
        self.refresh_single_file(path, prefer_staged=True)

    def revert_hunk_or_lines(
        self, hunk_index: int, selected_lines: set[int] | None = None
    ) -> None:
        """Revert a hunk or selected lines in the current file."""
        if self.current_diff is None or self.current_entry is None:
            return
        path = self.current_entry.path
        is_staged = self.current_entry.staged
        self.vcs.revert_hunk_or_lines(
            self.current_diff,
            hunk_index,
            selected_lines,
            staged=is_staged,
        )
        self.refresh_single_file(path, prefer_staged=is_staged)

    def set_amend(self, amend: bool) -> None:
        """Toggle amend mode on the VCS backend and rescan staged files."""
        if self.vcs.amend_mode == amend:
            return
        self.vcs.amend_mode = amend
        self.rescan()

    def commit(self, message: str, amend: bool = False) -> str:
        """Commit currently staged files and rescan repository status."""
        output = self.vcs.commit(message, self.staged, amend=amend)
        self.vcs.amend_mode = False
        self.rescan()
        return output

    def build_tool_argv(self, tool: ToolSpec) -> list[str]:
        """Build expanded argv for `tool` using the current cursor context."""
        placeholders = build_placeholder_map(
            ctx=self.cursor,
            repo_root=self.vcs.repo_root,
            vcs_name=self.vcs.name,
            branch=self.branch_name,
        )
        return expand_tool_command(tool.command, placeholders)
