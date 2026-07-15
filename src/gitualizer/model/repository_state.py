from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


ReferenceKind = Literal["local_branch", "remote_tracking", "tag", "head", "other"]
ChangeArea = Literal["staged", "working_tree", "untracked", "conflict"]


@dataclass(frozen=True)
class Commit:
    oid: str
    short_oid: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    author_date: str
    subject: str


@dataclass(frozen=True)
class Reference:
    name: str
    full_name: str
    target: str
    kind: ReferenceKind
    upstream: Optional[str] = None
    ahead: Optional[int] = None
    behind: Optional[int] = None


@dataclass(frozen=True)
class Remote:
    name: str
    fetch_url: Optional[str] = None
    push_url: Optional[str] = None


@dataclass(frozen=True)
class FileChange:
    path: str
    area: ChangeArea
    code: str
    original_path: Optional[str] = None


@dataclass(frozen=True)
class HeadState:
    oid: Optional[str]
    short_oid: Optional[str]
    branch: Optional[str]
    detached: bool
    unborn: bool = False


@dataclass(frozen=True)
class OperationState:
    merge: bool = False
    rebase: bool = False
    cherry_pick: bool = False
    revert: bool = False
    bisect: bool = False

    @property
    def active_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.merge:
            labels.append("merge")
        if self.rebase:
            labels.append("rebase")
        if self.cherry_pick:
            labels.append("cherry-pick")
        if self.revert:
            labels.append("revert")
        if self.bisect:
            labels.append("bisect")
        return tuple(labels)


@dataclass(frozen=True)
class RepositoryState:
    path: Path
    git_dir: Path
    head: HeadState
    commits: dict[str, Commit] = field(default_factory=dict)
    references: list[Reference] = field(default_factory=list)
    remotes: list[Remote] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    operation: OperationState = field(default_factory=OperationState)
    commit_limit: int = 300
    commits_truncated: bool = False

    @property
    def local_branches(self) -> list[Reference]:
        return [ref for ref in self.references if ref.kind == "local_branch"]

    @property
    def remote_tracking_branches(self) -> list[Reference]:
        return [ref for ref in self.references if ref.kind == "remote_tracking"]

    @property
    def tags(self) -> list[Reference]:
        return [ref for ref in self.references if ref.kind == "tag"]

    @property
    def staged_changes(self) -> list[FileChange]:
        return [change for change in self.changes if change.area == "staged"]

    @property
    def working_tree_changes(self) -> list[FileChange]:
        return [change for change in self.changes if change.area == "working_tree"]

    @property
    def untracked_changes(self) -> list[FileChange]:
        return [change for change in self.changes if change.area == "untracked"]

    @property
    def conflicted_changes(self) -> list[FileChange]:
        return [change for change in self.changes if change.area == "conflict"]
