"""VCS auto-detection for Git and Subversion working copies."""

import subprocess
from pathlib import Path

from reviewtool.vcs.base import VCSBackend, VCSError
from reviewtool.vcs.git import GitBackend
from reviewtool.vcs.svn import SvnBackend


def detect_vcs(start_path: Path | None = None) -> VCSBackend:
    """Detect whether `start_path` is inside a Git or SVN repository."""
    target = (start_path or Path.cwd()).resolve()
    if target.is_file():
        target = target.parent

    for parent in (target, *target.parents):
        if (parent / ".git").exists():
            return GitBackend(parent)
        if (parent / ".svn").exists():
            return SvnBackend(parent)

    git_proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )
    if git_proc.returncode == 0 and git_proc.stdout.strip():
        return GitBackend(Path(git_proc.stdout.strip()))

    svn_proc = subprocess.run(
        ["svn", "info", "--show-item", "wc-root"],
        cwd=target,
        text=True,
        capture_output=True,
        check=False,
    )
    if svn_proc.returncode == 0 and svn_proc.stdout.strip():
        return SvnBackend(Path(svn_proc.stdout.strip()))

    raise VCSError(f"No Git or SVN repository found at or above {target}")


__all__ = [
    "GitBackend",
    "SvnBackend",
    "VCSBackend",
    "VCSError",
    "detect_vcs",
]
