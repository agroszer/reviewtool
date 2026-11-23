"""Integration tests for GitBackend and ReviewSession caching/partial staging."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from reviewtool.config import ConfigManager
from reviewtool.state import ReviewSession
from reviewtool.vcs import GitBackend, detect_vcs


def _init_git_repo(repo_dir: Path) -> GitBackend:
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
    )
    (repo_dir / "app.py").write_text(
        "def one():\n    return 1\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )
    (repo_dir / "other.py").write_text("x = 10\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    return GitBackend(repo_dir)


def test_git_detect_and_status_and_partial_line_staging(tmp_path: Path) -> None:
    backend = _init_git_repo(tmp_path)
    detected = detect_vcs(tmp_path)
    assert isinstance(detected, GitBackend)

    (tmp_path / "app.py").write_text(
        "def one():\n    return 10\n    # extra line\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "untracked.py").write_text("new_var = 99\n", encoding="utf-8")

    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()
    session = ReviewSession(vcs=backend, config=cfg)
    session.rescan()

    unstaged_paths = [e.path for e in session.unstaged]
    assert "app.py" in unstaged_paths
    assert "untracked.py" in unstaged_paths
    assert len(session.staged) == 0

    app_entry = next(e for e in session.unstaged if e.path == "app.py")
    fdiff = session.select_entry(app_entry)
    assert fdiff is not None
    assert len(fdiff.hunks) == 1

    with patch.object(
        backend, "scan_status", wraps=backend.scan_status
    ) as mock_full_scan:
        session.toggle_viewed("app.py")
        assert session.is_viewed("app.py") is True
        session.select_entry(app_entry)
        assert mock_full_scan.call_count == 0

        session.stage_hunk_or_lines(0, selected_lines={1, 2})
        assert mock_full_scan.call_count == 0

    assert any(e.path == "app.py" for e in session.staged)
    assert any(e.path == "app.py" for e in session.unstaged)

    staged_app = next(e for e in session.staged if e.path == "app.py")
    s_diff = session.select_entry(staged_app)
    assert s_diff is not None
    added_texts = [
        ln.text for ln in s_diff.hunks[0].lines if ln.kind == "add"
    ]
    assert "    return 10" in added_texts
    assert "    # extra line" not in added_texts

    u_app = next(e for e in session.unstaged if e.path == "app.py")
    session.select_entry(u_app)
    session.revert_hunk_or_lines(0)
    assert not any(e.path == "app.py" for e in session.unstaged)

    session.commit("Update one() return value")
    assert not any(e.path == "app.py" for e in session.staged)
    assert backend.get_last_commit_message() == "Update one() return value"


def test_git_unstage_on_unborn_head_with_subsequent_edits(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    f = tmp_path / "state.py"
    f.write_text("v = 1\n", encoding="utf-8")
    backend = GitBackend(tmp_path)
    backend.stage_file("state.py")
    f.write_text("v = 2\n", encoding="utf-8")

    backend.unstage_file("state.py")
    unstaged, staged = backend.scan_status()
    assert len(staged) == 0
    assert [e.path for e in unstaged] == ["state.py"]
    assert f.read_text(encoding="utf-8") == "v = 2\n"


def test_git_amend_populates_staged_from_prev_commit(tmp_path: Path) -> None:
    backend = _init_git_repo(tmp_path)
    (tmp_path / "app.py").write_text(
        "def one():\n    return 42\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )
    backend.stage_file("app.py")
    backend.commit("Change one to 42", list(backend.scan_status()[1]))

    cfg_dir = tmp_path.parent / f"{tmp_path.name}_cfg"
    cfg_dir.mkdir(exist_ok=True)
    cfg = ConfigManager(
        config_path=cfg_dir / "config.ini",
        tools_path=cfg_dir / "tools.ini",
    )
    cfg.load()
    session = ReviewSession(vcs=backend, config=cfg)
    session.rescan()
    assert session.staged == []

    session.set_amend(True)
    assert [e.path for e in session.staged] == ["app.py"]
    s_diff = session.select_entry(session.staged[0])
    assert s_diff is not None
    added = [ln.text for ln in s_diff.hunks[0].lines if ln.kind == "add"]
    assert "    return 42" in added

    session.unstage_file("app.py")
    assert session.staged == []
    assert [e.path for e in session.unstaged] == ["app.py"]

    session.stage_file("app.py")
    assert [e.path for e in session.staged] == ["app.py"]

    session.set_amend(False)
    assert session.staged == []


def test_git_push_to_bare_remote(tmp_path: Path) -> None:
    remote_dir = tmp_path / "remote.git"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    subprocess.run(
        ["git", "init", "--bare", str(remote_dir)],
        check=True,
        capture_output=True,
    )
    backend = _init_git_repo(work_dir)
    branch = backend.get_branch_or_info()
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote_dir)],
        cwd=work_dir,
        check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=work_dir,
        check=True,
        capture_output=True,
    )

    (work_dir / "other.py").write_text("x = 20\n", encoding="utf-8")
    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()
    session = ReviewSession(vcs=backend, config=cfg)
    session.rescan()
    session.stage_file("other.py")
    session.commit("Bump x")
    assert backend.supports_push is True
    out = session.push()
    assert out


