"""Subversion (SVN) VCS backend wrapping the `svn` CLI via subprocess."""

import getpass
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from reviewtool.diff import (
    apply_reverse_hunk_to_lines,
    parse_unified_diff,
    synthesize_untracked_diff,
)
from reviewtool.models import FileDiff, FileEntry
from reviewtool.vcs.base import VCSBackend, VCSError

_SVN_STATUS_MAP: dict[str, str] = {
    "modified": "M",
    "added": "A",
    "deleted": "D",
    "unversioned": "?",
    "missing": "!",
    "replaced": "R",
    "conflicted": "C",
}


class SvnBackend(VCSBackend):
    """Subversion repository backend with virtual staging set for commit selection."""

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo_root)
        self._staged_paths: set[str] = set()

    @property
    def name(self) -> str:
        return "svn"

    @property
    def supports_amend(self) -> bool:
        return False

    def _run_svn(
        self,
        args: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["svn", "--non-interactive", *args]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            parts = [
                p.strip()
                for p in (proc.stdout, proc.stderr)
                if p and p.strip()
            ]
            err = "\n".join(parts) or "svn command failed"
            raise VCSError(f"svn {' '.join(args)}:\n{err}")
        return proc

    def get_branch_or_info(self) -> str:
        proc = self._run_svn(["info", "--show-item", "relative-url"], check=False)
        rel_url = proc.stdout.strip()
        rev_proc = self._run_svn(["info", "--show-item", "revision"], check=False)
        rev = rev_proc.stdout.strip()
        if rel_url and rev:
            return f"{rel_url}@{rev}"
        return rel_url or (f"r{rev}" if rev else "svn")

    def scan_status(self) -> tuple[list[FileEntry], list[FileEntry]]:
        proc = self._run_svn(["status", "--xml"])
        status_by_path = self._parse_status_xml(proc.stdout)
        self._staged_paths.intersection_update(status_by_path.keys())

        unstaged: list[FileEntry] = []
        staged: list[FileEntry] = []
        for path, code in sorted(status_by_path.items()):
            if path in self._staged_paths:
                staged.append(FileEntry(path=path, status=code, staged=True))
            else:
                unstaged.append(FileEntry(path=path, status=code, staged=False))
        return unstaged, staged

    def scan_file_status(
        self, rel_path: str
    ) -> tuple[FileEntry | None, FileEntry | None]:
        proc = self._run_svn(["status", "--xml", "--depth=empty", "--", rel_path])
        status_by_path = self._parse_status_xml(proc.stdout)
        code = status_by_path.get(rel_path)
        if code is None:
            self._staged_paths.discard(rel_path)
            return None, None
        if rel_path in self._staged_paths:
            return None, FileEntry(path=rel_path, status=code, staged=True)
        return FileEntry(path=rel_path, status=code, staged=False), None

    def _parse_status_xml(self, xml_text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        if not xml_text.strip():
            return result
        root = ET.fromstring(xml_text)
        for entry_el in root.findall(".//entry"):
            raw_path = entry_el.attrib.get("path", "")
            if not raw_path:
                continue
            wc_el = entry_el.find("wc-status")
            if wc_el is None:
                continue
            item = wc_el.attrib.get("item", "")
            props = wc_el.attrib.get("props", "none")
            code = _SVN_STATUS_MAP.get(item)
            if code is None and props in ("modified", "conflicted"):
                code = "M"
            if code is None:
                continue
            abs_p = (self.repo_root / raw_path).resolve()
            if abs_p.is_dir() and code == "?":
                for child in sorted(abs_p.rglob("*")):
                    if child.is_file():
                        rel_child = child.relative_to(self.repo_root).as_posix()
                        result[rel_child] = "?"
            else:
                norm_path = Path(raw_path).as_posix()
                result[norm_path] = code
        return result

    def get_diff(
        self,
        entry: FileEntry,
        context_lines: int = 3,
        full_file: bool = False,
    ) -> FileDiff:
        abs_path = self.repo_root / entry.path
        if entry.status == "?":
            return synthesize_untracked_diff(abs_path, entry.path)

        ctx = 999999 if full_file else max(0, context_lines)
        proc = self._run_svn(
            ["diff", "-x", f"-U{ctx}", "--", entry.path], check=False
        )
        fdiff = parse_unified_diff(proc.stdout, entry.path)
        if abs_path.is_file():
            raw_bytes = abs_path.read_bytes()
            if b"\x00" not in raw_bytes[:8192]:
                fdiff.total_new_lines = len(
                    raw_bytes.decode("utf-8", errors="replace").splitlines()
                )
        return fdiff

    def get_working_file_lines(self, rel_path: str) -> list[str]:
        abs_path = self.repo_root / rel_path
        if not abs_path.is_file():
            return []
        return abs_path.read_text(encoding="utf-8", errors="replace").splitlines()

    def stage_file(self, rel_path: str) -> None:
        u_entry, s_entry = self.scan_file_status(rel_path)
        entry = u_entry or s_entry
        if entry is not None:
            if entry.status == "?":
                self._run_svn(["add", "--parents", "--", rel_path])
            elif entry.status == "!":
                self._run_svn(["rm", "--", rel_path])
        self._staged_paths.add(rel_path)

    def unstage_file(self, rel_path: str) -> None:
        self._staged_paths.discard(rel_path)

    def stage_all(self, entries: list[FileEntry]) -> None:
        for entry in entries:
            self.stage_file(entry.path)

    def unstage_all(self, entries: list[FileEntry]) -> None:
        for entry in entries:
            self._staged_paths.discard(entry.path)

    def revert_file(self, entry: FileEntry) -> None:
        self._staged_paths.discard(entry.path)
        abs_path = self.repo_root / entry.path
        if entry.status == "?":
            if abs_path.is_file() or abs_path.is_symlink():
                abs_path.unlink()
            return
        self._run_svn(["revert", "--", entry.path])
        if entry.status == "A" and (abs_path.is_file() or abs_path.is_symlink()):
            abs_path.unlink()

    def stage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        self.stage_file(file_diff.path)

    def unstage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        self.unstage_file(file_diff.path)

    def revert_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
        staged: bool = False,
    ) -> None:
        if hunk_index < 0 or hunk_index >= len(file_diff.hunks):
            raise IndexError(f"Hunk index {hunk_index} out of range")
        abs_path = self.repo_root / file_diff.path
        if not abs_path.is_file():
            return
        raw_text = abs_path.read_text(encoding="utf-8", errors="replace")
        trailing_newline = raw_text.endswith("\n")
        working_lines = raw_text.splitlines()
        hunk = file_diff.hunks[hunk_index]
        updated_lines = apply_reverse_hunk_to_lines(
            working_lines, hunk, selected_line_indices=selected_lines
        )
        new_content = "\n".join(updated_lines)
        if trailing_newline and updated_lines:
            new_content += "\n"
        abs_path.write_text(new_content, encoding="utf-8")

    def commit(
        self,
        message: str,
        staged_entries: list[FileEntry],
        amend: bool = False,
    ) -> str:
        if amend:
            raise VCSError("Subversion does not support amending commits.")
        msg = message.strip()
        if not msg:
            raise VCSError("Commit message cannot be empty.")
        if not staged_entries:
            raise VCSError("No staged files selected for SVN commit.")
        paths = [entry.path for entry in staged_entries]
        proc = self._run_svn(["commit", "-m", msg, "--", *paths])
        for path in paths:
            self._staged_paths.discard(path)
        parts = [
            p.strip() for p in (proc.stdout, proc.stderr) if p and p.strip()
        ]
        return "\n".join(parts) or "OK"

    def get_last_commit_message(self) -> str:
        return ""

    def get_signoff_line(self) -> str:
        user = getpass.getuser()
        return f"Signed-off-by: {user}"
