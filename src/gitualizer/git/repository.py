from __future__ import annotations

from pathlib import Path
import re
from typing import Optional, Union

from gitualizer.git.runner import GitRunner
from gitualizer.model.repository_state import (
    Commit,
    FileChange,
    HeadState,
    OperationState,
    Reference,
    Remote,
    RepositoryState,
)


TRACK_RE = re.compile(r"\[(?:(ahead) (\d+))?(?:, )?(?:(behind) (\d+))?\]")


class RepositoryReader:
    def __init__(self, runner: Optional[GitRunner] = None) -> None:
        self.runner = runner or GitRunner()

    def read(self, path: Union[Path, str], commit_limit: int = 300) -> RepositoryState:
        requested_path = Path(path).expanduser().resolve()
        root = self._repository_root(requested_path)
        git_dir = Path(self.runner.run(["rev-parse", "--absolute-git-dir"], cwd=root).stdout.strip())
        head = self._read_head(root)
        references = self._read_references(root)
        commits, commits_truncated = self._read_commits(root, commit_limit)
        remotes = self._read_remotes(root)
        changes = self._read_changes(root)
        operation = self._read_operation_state(git_dir)
        return RepositoryState(
            path=root,
            git_dir=git_dir,
            head=head,
            commits=commits,
            references=references,
            remotes=remotes,
            changes=changes,
            operation=operation,
            commit_limit=commit_limit,
            commits_truncated=commits_truncated,
        )

    def _repository_root(self, path: Path) -> Path:
        result = self.runner.run(["rev-parse", "--show-toplevel"], cwd=path)
        return Path(result.stdout.strip()).resolve()

    def _read_head(self, root: Path) -> HeadState:
        branch_result = self.runner.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root, check=False)
        oid_result = self.runner.run(["rev-parse", "--verify", "HEAD"], cwd=root, check=False)
        if oid_result.returncode != 0:
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
            return HeadState(oid=None, short_oid=None, branch=branch, detached=False, unborn=True)
        oid = oid_result.stdout.strip()
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        return HeadState(
            oid=oid,
            short_oid=oid[:12],
            branch=branch,
            detached=branch is None,
            unborn=False,
        )

    def _read_references(self, root: Path) -> list[Reference]:
        fmt = "%(refname)%1f%(refname:short)%1f%(objectname)%1f%(upstream:short)%1f%(upstream:track)%1e"
        result = self.runner.run(["for-each-ref", f"--format={fmt}", "refs/heads", "refs/remotes", "refs/tags"], cwd=root)
        references: list[Reference] = []
        for record in result.stdout.split("\x1e"):
            if not record.strip():
                continue
            full_name, short_name, target, upstream, track = (record.strip("\n").split("\x1f") + [""] * 5)[:5]
            if full_name.startswith("refs/heads/"):
                kind = "local_branch"
            elif full_name.startswith("refs/remotes/"):
                kind = "remote_tracking"
            elif full_name.startswith("refs/tags/"):
                kind = "tag"
            else:
                kind = "other"
            ahead, behind = self._parse_track(track)
            references.append(
                Reference(
                    name=short_name,
                    full_name=full_name,
                    target=target,
                    kind=kind,
                    upstream=upstream or None,
                    ahead=ahead,
                    behind=behind,
                )
            )
        return sorted(references, key=lambda ref: (ref.kind, ref.name))

    def _parse_track(self, value: str) -> tuple[Optional[int], Optional[int]]:
        if not value:
            return None, None
        if value.strip() == "[gone]":
            return None, None
        match = TRACK_RE.search(value)
        if not match:
            return 0, 0
        ahead = 0
        behind = 0
        groups = match.groups()
        if groups[0] == "ahead" and groups[1]:
            ahead = int(groups[1])
        if groups[2] == "behind" and groups[3]:
            behind = int(groups[3])
        return ahead, behind

    def _read_commits(self, root: Path, commit_limit: int) -> tuple[dict[str, Commit], bool]:
        fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"
        limit = max(1, commit_limit + 1)
        result = self.runner.run(["log", "--all", "--date-order", f"--max-count={limit}", f"--format={fmt}"], cwd=root, check=False)
        if result.returncode != 0:
            return {}, False
        commits: dict[str, Commit] = {}
        seen = 0
        truncated = False
        for record in result.stdout.split("\x1e"):
            if not record.strip():
                continue
            seen += 1
            if seen > commit_limit:
                truncated = True
                break
            fields = (record.strip("\n").split("\x1f") + [""] * 6)[:6]
            oid, parents, author_name, author_email, author_date, subject = fields
            commits[oid] = Commit(
                oid=oid,
                short_oid=oid[:12],
                parents=tuple(parent for parent in parents.split(" ") if parent),
                author_name=author_name,
                author_email=author_email,
                author_date=author_date,
                subject=subject,
            )
        return commits, truncated

    def _read_remotes(self, root: Path) -> list[Remote]:
        result = self.runner.run(["remote", "-v"], cwd=root, check=False)
        remotes: dict[str, Remote] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            name, url, kind = parts
            current = remotes.get(name, Remote(name=name))
            if kind == "(fetch)":
                current = Remote(name=name, fetch_url=url, push_url=current.push_url)
            elif kind == "(push)":
                current = Remote(name=name, fetch_url=current.fetch_url, push_url=url)
            remotes[name] = current
        return [remotes[name] for name in sorted(remotes)]

    def _read_changes(self, root: Path) -> list[FileChange]:
        result = self.runner.run(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root)
        records = result.stdout.split("\0")
        changes: list[FileChange] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if len(record) < 4:
                continue
            x_status = record[0]
            y_status = record[1]
            path = record[3:]
            original_path = None
            if x_status in {"R", "C"} or y_status in {"R", "C"}:
                if index < len(records):
                    original_path = records[index] or None
                    index += 1
            code = f"{x_status}{y_status}"
            if code == "??":
                changes.append(FileChange(path=path, area="untracked", code=code))
                continue
            if code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
                changes.append(FileChange(path=path, area="conflict", code=code, original_path=original_path))
                continue
            if x_status != " ":
                changes.append(FileChange(path=path, area="staged", code=x_status, original_path=original_path))
            if y_status != " ":
                changes.append(FileChange(path=path, area="working_tree", code=y_status, original_path=original_path))
        return changes

    def _read_operation_state(self, git_dir: Path) -> OperationState:
        return OperationState(
            merge=(git_dir / "MERGE_HEAD").exists(),
            rebase=(git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists(),
            cherry_pick=(git_dir / "CHERRY_PICK_HEAD").exists(),
            revert=(git_dir / "REVERT_HEAD").exists(),
            bisect=(git_dir / "BISECT_LOG").exists(),
        )
