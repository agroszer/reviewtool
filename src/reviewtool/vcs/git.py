"""Git VCS backend wrapping the `git` CLI via subprocess."""

import getpass
import shlex
import socket
import subprocess
from pathlib import Path

from reviewtool.diff import (
    build_partial_patch,
    parse_unified_diff,
    synthesize_untracked_diff,
)
from reviewtool.models import FileDiff, FileEntry
from reviewtool.vcs.base import VCSBackend, VCSError


class GitBackend(VCSBackend):
    """Git repository backend using subprocess calls to `git`."""

    _EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    def __init__(self, repo_root: Path) -> None:
        super().__init__(repo_root)
        self._amend = False

    @property
    def name(self) -> str:
        return "git"

    @property
    def supports_amend(self) -> bool:
        return True

    @property
    def supports_push(self) -> bool:
        return True

    @property
    def amend_mode(self) -> bool:
        return self._amend

    @amend_mode.setter
    def amend_mode(self, value: bool) -> None:
        self._amend = value

    def _run_git(
        self,
        args: list[str],
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = ["git", *args]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_root,
            input=input_text,
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
            err = "\n".join(parts) or "git command failed"
            raise VCSError(f"git {' '.join(args)}:\n{err}")
        return proc

    def _has_head(self) -> bool:
        proc = self._run_git(["rev-parse", "--verify", "HEAD"], check=False)
        return proc.returncode == 0

    def _base_tree(self) -> str | None:
        if not self._has_head():
            return None
        if not self._amend:
            return "HEAD"
        proc = self._run_git(["rev-parse", "--verify", "HEAD^"], check=False)
        return "HEAD^" if proc.returncode == 0 else self._EMPTY_TREE

    def get_branch_or_info(self) -> str:
        proc = self._run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], check=False
        )
        branch = proc.stdout.strip()
        if proc.returncode == 0 and branch:
            return branch
        proc_sym = self._run_git(["symbolic-ref", "--short", "HEAD"], check=False)
        return proc_sym.stdout.strip() or "HEAD (unborn)"

    def scan_status(self) -> tuple[list[FileEntry], list[FileEntry]]:
        proc = self._run_git(["status", "--porcelain=v1", "-z", "-uall"])
        unstaged, staged = self._parse_porcelain_z(proc.stdout)
        if self._amend and self._has_head():
            staged = self._scan_amend_staged()
        return unstaged, staged

    def scan_file_status(
        self, rel_path: str
    ) -> tuple[FileEntry | None, FileEntry | None]:
        proc = self._run_git(
            ["status", "--porcelain=v1", "-z", "-uall", "--", rel_path]
        )
        unstaged, staged = self._parse_porcelain_z(proc.stdout)
        if self._amend and self._has_head():
            staged = self._scan_amend_staged(rel_path)
        u_entry = unstaged[0] if unstaged else None
        s_entry = staged[0] if staged else None
        return u_entry, s_entry

    def _scan_amend_staged(
        self, rel_path: str | None = None
    ) -> list[FileEntry]:
        base = self._base_tree()
        if base is None:
            return []
        args = ["diff-index", "--cached", "-z", "-M", base]
        if rel_path is not None:
            args.extend(["--", rel_path])
        proc = self._run_git(args)
        return self._parse_diff_index_z(proc.stdout)

    def _parse_diff_index_z(self, raw: str) -> list[FileEntry]:
        staged: list[FileEntry] = []
        if not raw:
            return staged
        tokens = raw.split("\x00")
        idx = 0
        total = len(tokens)
        while idx < total:
            meta = tokens[idx]
            idx += 1
            if not meta.startswith(":"):
                continue
            status_part = meta.split(" ")[-1]
            code = status_part[0]
            if code in ("R", "C") and idx + 1 < total:
                orig_path = tokens[idx]
                path = tokens[idx + 1]
                idx += 2
                staged.append(
                    FileEntry(
                        path=path,
                        status=code,
                        staged=True,
                        orig_path=orig_path,
                    )
                )
            elif idx < total:
                path = tokens[idx]
                idx += 1
                staged.append(
                    FileEntry(path=path, status=code, staged=True)
                )
        return staged

    def _parse_porcelain_z(
        self, raw: str
    ) -> tuple[list[FileEntry], list[FileEntry]]:
        unstaged: list[FileEntry] = []
        staged: list[FileEntry] = []
        if not raw:
            return unstaged, staged

        tokens = raw.split("\x00")
        idx = 0
        total = len(tokens)
        while idx < total:
            record = tokens[idx]
            idx += 1
            if len(record) < 4:
                continue
            x_code = record[0]
            y_code = record[1]
            path = record[3:]
            orig_path: str | None = None
            if x_code in ("R", "C") or y_code in ("R", "C"):
                if idx < total:
                    orig_path = tokens[idx]
                    idx += 1

            if x_code == "?" and y_code == "?":
                unstaged.append(
                    FileEntry(path=path, status="?", staged=False)
                )
                continue

            if x_code not in (" ", "?"):
                staged.append(
                    FileEntry(
                        path=path,
                        status=x_code,
                        staged=True,
                        orig_path=orig_path,
                    )
                )
            if y_code not in (" ", "?"):
                unstaged.append(
                    FileEntry(
                        path=path,
                        status=y_code,
                        staged=False,
                        orig_path=orig_path,
                    )
                )
        return unstaged, staged

    def get_diff(
        self,
        entry: FileEntry,
        context_lines: int = 3,
        full_file: bool = False,
    ) -> FileDiff:
        abs_path = self.repo_root / entry.path
        if entry.status == "?" and not entry.staged:
            return synthesize_untracked_diff(abs_path, entry.path)

        ctx = 999999 if full_file else max(0, context_lines)
        args = ["diff", "--no-color", "--no-ext-diff", f"-U{ctx}"]
        if entry.staged:
            args.append("--cached")
            base = self._base_tree()
            if self._amend and base is not None:
                args.append(base)
        args.extend(["--", entry.path])
        proc = self._run_git(args)
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
        self._run_git(["add", "--", rel_path])

    def unstage_file(self, rel_path: str) -> None:
        base = self._base_tree()
        if base is not None and base != self._EMPTY_TREE:
            self._run_git(["reset", base, "--", rel_path])
        else:
            self._run_git(["rm", "-f", "--cached", "--", rel_path])

    def stage_all(self, entries: list[FileEntry]) -> None:
        if not entries:
            return
        self._run_git(["add", "-A"])

    def unstage_all(self, entries: list[FileEntry]) -> None:
        if not entries:
            return
        base = self._base_tree()
        if base is not None and base != self._EMPTY_TREE:
            self._run_git(["reset", base])
        else:
            self._run_git(["rm", "-rf", "--cached", "."])

    def revert_file(self, entry: FileEntry) -> None:
        abs_path = self.repo_root / entry.path
        if entry.status == "?" and not entry.staged:
            if abs_path.is_file() or abs_path.is_symlink():
                abs_path.unlink()
            return
        if entry.staged:
            self.unstage_file(entry.path)
            if entry.status == "A":
                return
        self._run_git(["checkout", "--", entry.path])

    def stage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        if file_diff.is_untracked:
            self.stage_file(file_diff.path)
            return
        patch = build_partial_patch(
            file_diff,
            hunk_index=hunk_index,
            selected_line_indices=selected_lines,
            reverse=False,
        )
        if not patch:
            return
        self._run_git(
            ["apply", "--cached", "--recount", "--unidiff-zero", "-"],
            input_text=patch,
        )

    def unstage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        patch = build_partial_patch(
            file_diff,
            hunk_index=hunk_index,
            selected_line_indices=selected_lines,
            reverse=True,
        )
        if not patch:
            return
        self._run_git(
            ["apply", "--cached", "--reverse", "--recount", "--unidiff-zero", "-"],
            input_text=patch,
        )

    def revert_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
        staged: bool = False,
    ) -> None:
        patch = build_partial_patch(
            file_diff,
            hunk_index=hunk_index,
            selected_line_indices=selected_lines,
            reverse=True,
        )
        if not patch:
            return
        args = ["apply", "--reverse", "--recount", "--unidiff-zero"]
        if staged:
            args.append("--index")
        args.append("-")
        self._run_git(args, input_text=patch)

    def format_commit_command(
        self,
        message: str,
        staged_entries: list[FileEntry],
        amend: bool = False,
    ) -> str:
        args = ["git", "commit", "-m", message.strip()]
        if amend:
            args.append("--amend")
        return shlex.join(args)

    def format_push_command(self) -> str:
        return "git push"

    def commit(
        self,
        message: str,
        staged_entries: list[FileEntry],
        amend: bool = False,
    ) -> str:
        msg = message.strip()
        if not msg:
            raise VCSError("Commit message cannot be empty.")
        if not staged_entries and not amend:
            raise VCSError("No staged changes to commit.")
        args = ["commit", "-m", msg]
        if amend:
            args.append("--amend")
        proc = self._run_git(args)
        parts = [
            p.strip() for p in (proc.stdout, proc.stderr) if p and p.strip()
        ]
        return "\n".join(parts) or "OK"

    def push(self) -> str:
        proc = self._run_git(["push"])
        parts = [
            p.strip() for p in (proc.stdout, proc.stderr) if p and p.strip()
        ]
        return "\n".join(parts) or "OK"

    def get_last_commit_message(self) -> str:
        if not self._has_head():
            return ""
        proc = self._run_git(["log", "--max-count=1", "--format=%B"], check=False)
        return proc.stdout.strip()

    def get_signoff_line(self) -> str:
        name_proc = self._run_git(["config", "user.name"], check=False)
        email_proc = self._run_git(["config", "user.email"], check=False)
        name = name_proc.stdout.strip() or getpass.getuser()
        email = email_proc.stdout.strip() or f"{name}@{socket.gethostname()}"
        return f"Signed-off-by: {name} <{email}>"
