"""Git for Willy (pc_client/tools/git_ops.py) on a scratch repository."""

import subprocess

from pc_client.tools import git_ops


def _repo(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n")
    run("add", ".")
    run("commit", "-m", "first")
    return repo


def test_status_switch_restore_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(git_ops, "_roots", lambda: [tmp_path])
    git_ops._CACHE["at"] = 0
    assert [r["name"] for r in git_ops.repos(refresh=True)["repos"]] == ["demo"]
    assert "on main, clean" in git_ops.git_action("status", "demo")["message"]

    assert git_ops.git_action("create_branch", "demo", branch="feature")["success"]
    (repo / "a.txt").write_text("changed\n")
    blocked = git_ops.git_action("switch", "demo", branch="main")
    assert blocked["success"] is False and "uncommitted" in blocked["error"]
    assert git_ops.git_action("restore", "demo", files=["a.txt"])["success"]
    assert (repo / "a.txt").read_text() == "one\n"
    assert git_ops.git_action("switch", "demo", branch="main")["success"]

    (repo / "b.txt").write_text("new\n")
    assert git_ops.git_action("commit", "demo", message="add b")["success"]
    assert "add b" in git_ops.git_action("log", "demo")["output"]
    assert git_ops.git_action("status", "nope")["success"] is False
