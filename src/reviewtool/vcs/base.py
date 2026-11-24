"""Abstract base class for VCS backends (Git and SVN)."""

from abc import ABC, abstractmethod
from pathlib import Path

from reviewtool.models import FileDiff, FileEntry


class VCSError(RuntimeError):
    """Raised when a VCS CLI command fails."""


class VCSBackend(ABC):
    """Interface for Git and SVN repository operations."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()

    @property
    @abstractmethod
    def name(self) -> str:
        """Return 'git' or 'svn'."""

    @property
    @abstractmethod
    def supports_amend(self) -> bool:
        """Return True if the VCS supports amending the last commit."""

    @property
    def supports_push(self) -> bool:
        """Return True if the VCS supports pushing commits to a remote."""
        return False

    @property
    def amend_mode(self) -> bool:
        """Return True if amend mode is currently active."""
        return False

    @amend_mode.setter
    def amend_mode(self, value: bool) -> None:
        """Enable or disable amend mode."""

    @abstractmethod
    def get_branch_or_info(self) -> str:
        """Return current branch name (Git) or URL/revision (SVN)."""

    @abstractmethod
    def scan_status(self) -> tuple[list[FileEntry], list[FileEntry]]:
        """Full status scan returning `(unstaged_files, staged_files)`."""

    @abstractmethod
    def scan_file_status(
        self, rel_path: str
    ) -> tuple[FileEntry | None, FileEntry | None]:
        """Targeted status check for a single path: `(unstaged_entry, staged_entry)`."""

    @abstractmethod
    def get_diff(
        self,
        entry: FileEntry,
        context_lines: int = 3,
        full_file: bool = False,
    ) -> FileDiff:
        """Return parsed FileDiff for a single FileEntry."""

    @abstractmethod
    def get_working_file_lines(self, rel_path: str) -> list[str]:
        """Return lines of the file in the working tree (or index if needed)."""

    @abstractmethod
    def stage_file(self, rel_path: str) -> None:
        """Stage a single file."""

    @abstractmethod
    def unstage_file(self, rel_path: str) -> None:
        """Unstage a single file."""

    @abstractmethod
    def stage_all(self, entries: list[FileEntry]) -> None:
        """Stage all provided unstaged entries."""

    @abstractmethod
    def unstage_all(self, entries: list[FileEntry]) -> None:
        """Unstage all provided staged entries."""

    @abstractmethod
    def revert_file(self, entry: FileEntry) -> None:
        """Revert changes in a single file (or delete if untracked)."""

    @abstractmethod
    def stage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        """Stage a single hunk or selected lines within a hunk."""

    @abstractmethod
    def unstage_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
    ) -> None:
        """Unstage a single hunk or selected lines within a hunk."""

    @abstractmethod
    def revert_hunk_or_lines(
        self,
        file_diff: FileDiff,
        hunk_index: int,
        selected_lines: set[int] | None = None,
        staged: bool = False,
    ) -> None:
        """Revert a single hunk or selected lines in the working copy."""

    @abstractmethod
    def commit(
        self,
        message: str,
        staged_entries: list[FileEntry],
        amend: bool = False,
    ) -> str:
        """Create a commit and return command output."""

    def format_commit_command(
        self,
        message: str,
        staged_entries: list[FileEntry],
        amend: bool = False,
    ) -> str:
        """Return display command string for the commit operation."""
        return f"{self.name} commit"

    def format_push_command(self) -> str:
        """Return display command string for the push operation."""
        return f"{self.name} push"

    def push(self) -> str:
        """Push commits to the remote repository and return command output."""
        raise VCSError(f"{self.name} does not support push.")

    @abstractmethod
    def get_last_commit_message(self) -> str:
        """Return the last commit message (used when Amend is toggled)."""

    @abstractmethod
    def get_signoff_line(self) -> str:
        """Return `Signed-off-by: Name <email>` string."""
