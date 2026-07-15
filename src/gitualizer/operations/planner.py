from __future__ import annotations

import hashlib
from typing import Optional

from gitualizer.model.repository_state import FileChange, Reference, RepositoryState
from gitualizer.operations.command_plan import CommandPlan, CommandStep


def state_fingerprint(state: RepositoryState) -> str:
    parts: list[str] = [
        f"path:{state.path}",
        f"head:{state.head.oid or ''}",
        f"branch:{state.head.branch or ''}",
    ]
    for ref in sorted(state.references, key=lambda item: item.full_name):
        parts.append(f"ref:{ref.full_name}:{ref.target}")
    for change in sorted(state.changes, key=lambda item: (item.area, item.path, item.code)):
        parts.append(f"change:{change.area}:{change.code}:{change.path}:{change.original_path or ''}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


class OperationPlanner:
    def switch_branch(self, state: RepositoryState, branch: str) -> CommandPlan:
        branch = branch.strip()
        if not branch:
            raise ValueError("Choose a branch to switch to.")
        if branch == state.head.branch:
            raise ValueError("That branch is already checked out.")
        return CommandPlan(
            title=f"Switch to {branch}",
            explanation=f"Change the working tree and HEAD to the local branch `{branch}`.",
            steps=[CommandStep(["git", "switch", branch], "Switch to the selected local branch.")],
            expected_effects=[f"HEAD moves to `{branch}`.", "Git may refuse if local changes would be overwritten."],
            state_fingerprint=state_fingerprint(state),
        )

    def create_branch(self, state: RepositoryState, branch: str) -> CommandPlan:
        branch = branch.strip()
        if not branch:
            raise ValueError("Enter a branch name.")
        if any(ref.name == branch and ref.kind == "local_branch" for ref in state.references):
            raise ValueError("A local branch with that name already exists.")
        start = state.head.short_oid or "the current unborn HEAD"
        return CommandPlan(
            title=f"Create branch {branch}",
            explanation=f"Create a new local branch named `{branch}` at {start}.",
            steps=[CommandStep(["git", "branch", branch], "Create the branch without switching to it.")],
            expected_effects=[f"`{branch}` will point at the current commit.", "The current branch will not change."],
            state_fingerprint=state_fingerprint(state),
        )

    def stage_all(self, state: RepositoryState) -> CommandPlan:
        if not state.working_tree_changes and not state.untracked_changes:
            raise ValueError("There are no working tree or untracked changes to stage.")
        return CommandPlan(
            title="Stage all changes",
            explanation="Move all current working tree changes into the staging area.",
            steps=[CommandStep(["git", "add", "-A"], "Stage modifications, deletions, and untracked files.")],
            expected_effects=["Files move from Working Tree to Staging Area."],
            state_fingerprint=state_fingerprint(state),
        )

    def stage_paths(self, state: RepositoryState, changes: list[FileChange]) -> CommandPlan:
        paths = _paths_for_changes(changes)
        if not paths:
            raise ValueError("Select one or more working tree or untracked files.")
        return CommandPlan(
            title="Stage selected files",
            explanation="Move the selected paths into the staging area.",
            steps=[CommandStep(["git", "add", "--", *paths], "Stage only the selected paths.")],
            expected_effects=["Selected files move from Working Tree to Staging Area."],
            state_fingerprint=state_fingerprint(state),
        )

    def unstage_all(self, state: RepositoryState) -> CommandPlan:
        if not state.staged_changes:
            raise ValueError("There are no staged changes to unstage.")
        return CommandPlan(
            title="Unstage all changes",
            explanation="Move all staged changes back out of the index.",
            steps=[CommandStep(["git", "restore", "--staged", "--", "."], "Unstage all staged paths.")],
            expected_effects=["Files move from Staging Area back to Working Tree."],
            state_fingerprint=state_fingerprint(state),
        )

    def unstage_paths(self, state: RepositoryState, changes: list[FileChange]) -> CommandPlan:
        paths = _paths_for_changes(changes)
        if not paths:
            raise ValueError("Select one or more staged files.")
        return CommandPlan(
            title="Unstage selected files",
            explanation="Move the selected staged paths back out of the index.",
            steps=[CommandStep(["git", "restore", "--staged", "--", *paths], "Unstage only the selected paths.")],
            expected_effects=["Selected files move from Staging Area back to Working Tree."],
            state_fingerprint=state_fingerprint(state),
        )

    def commit(self, state: RepositoryState, message: str) -> CommandPlan:
        message = message.strip()
        if not message:
            raise ValueError("Enter a commit message.")
        if not state.staged_changes:
            raise ValueError("There are no staged changes to commit.")
        return CommandPlan(
            title="Create commit",
            explanation="Create a new commit from the current staging area.",
            steps=[CommandStep(["git", "commit", "-m", message], "Commit staged changes with the provided message.")],
            expected_effects=["A new commit will be created.", "The current branch will move to that commit."],
            state_fingerprint=state_fingerprint(state),
        )

    def fetch(self, state: RepositoryState, remote: str) -> CommandPlan:
        remote = remote.strip()
        if not remote:
            raise ValueError("Choose a remote to fetch.")
        return CommandPlan(
            title=f"Fetch {remote}",
            explanation="Update local remote-tracking references from the selected remote.",
            steps=[CommandStep(["git", "fetch", remote], f"Fetch objects and update `{remote}/...` tracking refs.")],
            expected_effects=["Remote-tracking branches may move.", "Local branches are not modified by fetch."],
            remote_impact="Reads from remote; does not write to it.",
            state_fingerprint=state_fingerprint(state),
        )

    def fast_forward_current_branch(self, state: RepositoryState) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out.")
        if not current.upstream:
            raise ValueError("The current branch has no configured upstream.")
        return CommandPlan(
            title=f"Fast-forward {current.name}",
            explanation=f"Move `{current.name}` forward to `{current.upstream}` only if no merge commit is required.",
            steps=[
                CommandStep(["git", "fetch"], "Refresh remote-tracking references for configured remotes."),
                CommandStep(["git", "merge", "--ff-only", current.upstream], "Fast-forward to the upstream branch."),
            ],
            expected_effects=[f"`{current.name}` may move forward.", "Git will refuse if histories have diverged."],
            state_fingerprint=state_fingerprint(state),
        )

    def push_current_branch(self, state: RepositoryState) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out.")
        if not current.upstream or "/" not in current.upstream:
            raise ValueError("The current branch has no configured upstream remote branch.")
        remote, _remote_branch = current.upstream.split("/", 1)
        return CommandPlan(
            title=f"Push {current.name}",
            explanation=f"Update the configured upstream branch from local `{current.name}`.",
            steps=[CommandStep(["git", "push", remote, current.name], "Push the current branch to its upstream remote.")],
            expected_effects=["The remote branch may move forward if the push is a fast-forward."],
            warnings=["Git will reject this push if it is not a fast-forward."],
            remote_impact=f"May update `{current.upstream}` on `{remote}`.",
            state_fingerprint=state_fingerprint(state),
        )


def _current_branch_ref(state: RepositoryState) -> Optional[Reference]:
    if not state.head.branch:
        return None
    for ref in state.local_branches:
        if ref.name == state.head.branch:
            return ref
    return None


def _paths_for_changes(changes: list[FileChange]) -> list[str]:
    paths: list[str] = []
    for change in changes:
        if change.path not in paths:
            paths.append(change.path)
    return paths
