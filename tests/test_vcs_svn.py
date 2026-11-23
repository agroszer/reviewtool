"""Tests for SvnBackend XML parsing, virtual staging, and hunk/line revert."""

import shutil
import subprocess
from pathlib import Path

import pytest

from reviewtool.config import ConfigManager
from reviewtool.state import ReviewSession
from reviewtool.vcs.svn import SvnBackend

SAMPLE_SVN_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<status>
<target path=".">
<entry path="foo.py">
<wc-status item="modified" revision="3" props="none">
<commit revision="2"><author>adi</author></commit>
</wc-status>
</entry>
<entry path="new_file.py">
<wc-status item="unversioned" props="none"></wc-status>
</entry>
</target>
</status>
"""


def test_svn_parse_status_xml(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "new_file.py").write_text("b = 2\n", encoding="utf-8")
    svn = SvnBackend(tmp_path)
    parsed = svn._parse_status_xml(SAMPLE_SVN_XML)
    assert parsed == {"foo.py": "M", "new_file.py": "?"}


@pytest.mark.skipif(
    shutil.which("svn") is None or shutil.which("svnadmin") is None,
    reason="svn/svnadmin CLI not installed",
)
def test_svn_integration_workflow(tmp_path: Path) -> None:
    repo_dir = tmp_path / "svn_repo"
    wc_dir = tmp_path / "wc"
    subprocess.run(["svnadmin", "create", str(repo_dir)], check=True)
    repo_url = f"file://{repo_dir.resolve()}"
    subprocess.run(["svn", "checkout", repo_url, str(wc_dir)], check=True)

    (wc_dir / "hello.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    subprocess.run(["svn", "add", "hello.py"], cwd=wc_dir, check=True)
    subprocess.run(
        ["svn", "commit", "-m", "init"], cwd=wc_dir, check=True
    )

    (wc_dir / "hello.py").write_text("x = 10\ny = 2\n", encoding="utf-8")
    (wc_dir / "extra.py").write_text("z = 3\n", encoding="utf-8")

    svn = SvnBackend(wc_dir)
    cfg = ConfigManager(
        config_path=tmp_path / "config.ini",
        tools_path=tmp_path / "tools.ini",
    )
    cfg.load()
    session = ReviewSession(vcs=svn, config=cfg)
    session.rescan()

    assert len(session.unstaged) == 2
    assert len(session.staged) == 0

    session.stage_file("hello.py")
    assert [e.path for e in session.staged] == ["hello.py"]
    assert [e.path for e in session.unstaged] == ["extra.py"]

    session.commit("Update x in hello.py")
    assert len(session.staged) == 0
    assert [e.path for e in session.unstaged] == ["extra.py"]
