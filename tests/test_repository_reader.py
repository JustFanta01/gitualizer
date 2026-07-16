from __future__ import annotations

from pathlib import Path
import subprocess

from gitualizer.git.repository import RepositoryReader


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    return completed.stdout.strip()


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def init_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    return path


def test_reads_commits_refs_remotes_and_changes(tmp_path: Path) -> None:
    remote = init_repo(tmp_path / "remote")
    write(remote, "README.md", "initial\n")
    first_oid = commit(remote, "initial")
    write(remote, "remote.txt", "remote\n")
    second_oid = commit(remote, "remote commit")

    work = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(work))
    git(work, "config", "user.name", "Test User")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "switch", "-c", "feature")
    write(work, "feature.txt", "feature\n")
    feature_oid = commit(work, "feature commit")
    git(work, "tag", "v0")
    git(work, "switch", "main")

    write(work, "README.md", "initial\nworking tree edit\n")
    write(work, "staged.txt", "staged\n")
    git(work, "add", "staged.txt")
    write(work, "untracked.txt", "untracked\n")

    state = RepositoryReader().read(work)

    assert state.path == work.resolve()
    assert state.head.branch == "main"
    assert state.head.detached is False
    assert second_oid in state.commits
    assert first_oid in state.commits[second_oid].parents

    refs = {ref.name: ref for ref in state.references}
    assert refs["main"].target == second_oid
    assert refs["feature"].target == feature_oid
    assert refs["origin/main"].kind == "remote_tracking"
    assert refs["v0"].kind == "tag"

    assert state.remotes[0].name == "origin"
    assert state.remotes[0].fetch_url == str(remote)

    changes = {(change.area, change.path, change.code) for change in state.changes}
    assert ("working_tree", "README.md", "M") in changes
    assert ("staged", "staged.txt", "A") in changes
    assert ("untracked", "untracked.txt", "??") in changes


def test_reads_unborn_repository(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "empty")

    state = RepositoryReader().read(repo)

    assert state.head.unborn is True
    assert state.head.oid is None
    assert state.commits == {}
    assert state.references == []


def test_commit_loading_can_be_limited(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "limited")
    for index in range(5):
        write(repo, "file.txt", f"{index}\n")
        commit(repo, f"commit {index}")

    state = RepositoryReader().read(repo, commit_limit=3)

    assert len(state.commits) == 3
    assert state.commits_truncated is True
    assert state.commit_limit == 3


def test_recent_unreferenced_commits_are_loaded_from_reflog(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "reflog")
    write(repo, "file.txt", "base\n")
    base = commit(repo, "base")
    write(repo, "file.txt", "temporary\n")
    lost = commit(repo, "temporary")
    git(repo, "reset", "--hard", base)

    state = RepositoryReader().read(repo)

    assert lost in state.commits
    assert all(ref.target != lost for ref in state.references)


def test_reads_stashes(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "stashes")
    write(repo, "file.txt", "committed\n")
    commit(repo, "initial")
    write(repo, "file.txt", "stashed\n")
    git(repo, "stash", "push", "-m", "work in progress")

    state = RepositoryReader().read(repo)

    assert len(state.stashes) == 1
    assert state.stashes[0].ref == "stash@{0}"
    assert "work in progress" in state.stashes[0].subject
