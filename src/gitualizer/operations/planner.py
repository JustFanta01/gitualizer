from __future__ import annotations

import hashlib
from typing import Optional

from gitualizer.model.repository_state import Commit, FileChange, Reference, RepositoryState
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
            preview_steps=[f"HEAD detaches from `{state.head.branch or 'current position'}`.", f"HEAD attaches to `{branch}`."],
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
            preview_steps=[f"Create a new branch label `{branch}` at {start}."],
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
            preview_steps=["Working tree changes become index entries.", "No commit is created yet."],
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
            preview_steps=[f"Stage {len(paths)} selected path(s) into the index.", "HEAD and branch refs do not move."],
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
            preview_steps=["Index entries are restored from HEAD.", "File contents remain in the working tree."],
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
            preview_steps=[f"Move {len(paths)} staged path(s) out of the index.", "No file content is discarded."],
            state_fingerprint=state_fingerprint(state),
        )

    def discard_changes(self, state: RepositoryState, changes: list[FileChange]) -> CommandPlan:
        paths = _paths_for_changes(changes)
        if not paths:
            raise ValueError("Drag one or more changed files to the trash.")
        tracked_paths = _paths_for_changes([change for change in changes if change.area != "untracked"])
        untracked_paths = _paths_for_changes([change for change in changes if change.area == "untracked"])
        steps: list[CommandStep] = []
        if tracked_paths:
            steps.append(
                CommandStep(
                    ["git", "restore", "--staged", "--worktree", "--", *tracked_paths],
                    "Discard tracked staged and working-tree changes.",
                )
            )
        if untracked_paths:
            steps.append(CommandStep(["git", "clean", "-f", "--", *untracked_paths], "Remove untracked files."))
        return CommandPlan(
            title="Discard selected changes",
            explanation="Remove the selected changes from the working tree and/or index.",
            steps=steps,
            expected_effects=["Selected file changes disappear from the working tree and index."],
            preview_steps=["Remove selected uncommitted changes from the visible file-state graph."],
            warnings=["This is destructive. Discarded uncommitted file contents may not be recoverable from Git."],
            destructive=True,
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
            preview_steps=["Create a new commit node from the index.", f"Move `{state.head.branch or 'HEAD'}` to the new commit."],
            state_fingerprint=state_fingerprint(state),
        )

    def commit_to_branch(self, state: RepositoryState, branch: str, message: str) -> CommandPlan:
        branch = branch.strip()
        message = message.strip()
        if not branch:
            raise ValueError("Drop the staging area onto a local branch.")
        if not message:
            raise ValueError("Enter a commit message.")
        if not state.staged_changes:
            raise ValueError("There are no staged changes to commit.")
        steps: list[CommandStep] = []
        if state.head.branch != branch:
            steps.append(CommandStep(["git", "switch", branch], f"Make `{branch}` the current branch."))
        steps.append(CommandStep(["git", "commit", "-m", message], "Create a commit from the current staging area."))
        return CommandPlan(
            title=f"Commit staged changes to {branch}",
            explanation=f"Create a new commit on `{branch}` from the current staging area.",
            steps=steps,
            expected_effects=[
                "A new commit node will be created from the index.",
                f"`{branch}` will move to the new commit.",
            ],
            preview_steps=[
                f"Attach HEAD to `{branch}` if it is not already current.",
                "Create a new commit node from the staging area.",
                f"Move `{branch}` to the new commit.",
            ],
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
            preview_steps=[f"Update local remote-tracking refs for `{remote}`.", "Do not move local branch labels."],
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
            preview_steps=[
                "Refresh remote-tracking refs.",
                f"If possible, move `{current.name}` forward to `{current.upstream}`.",
                "No merge commit is created.",
            ],
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
            preview_steps=[f"Move the remote branch for `{current.name}` to the local branch tip if the remote accepts it."],
            warnings=["Git will reject this push if it is not a fast-forward."],
            remote_impact=f"May update `{current.upstream}` on `{remote}`.",
            state_fingerprint=state_fingerprint(state),
        )

    def integrate_remote_tracking(
        self,
        state: RepositoryState,
        remote_ref: Reference,
        local_ref: Reference,
        strategy: str,
    ) -> CommandPlan:
        if remote_ref.kind != "remote_tracking" or local_ref.kind != "local_branch":
            raise ValueError("Drag a remote-tracking branch onto a local branch.")
        remote = remote_ref.name.split("/", 1)[0]
        if strategy == "ff":
            title = f"Fast-forward {local_ref.name} from {remote_ref.name}"
            explanation = (
                f"Update local `{local_ref.name}` from `{remote_ref.name}` only if Git can move the branch "
                "pointer forward without creating a merge commit."
            )
            merge_args = ["git", "merge", "--ff-only", remote_ref.name]
            effects = [f"`{local_ref.name}` moves to `{remote_ref.name}` if it is a direct descendant."]
        elif strategy == "merge":
            title = f"Merge {remote_ref.name} into {local_ref.name}"
            explanation = f"Integrate `{remote_ref.name}` into `{local_ref.name}` while preserving both histories."
            merge_args = ["git", "merge", remote_ref.name]
            effects = [f"`{local_ref.name}` may get a merge commit.", "Both branch histories are preserved."]
        elif strategy == "rebase":
            title = f"Rebase {local_ref.name} onto {remote_ref.name}"
            explanation = f"Replay commits unique to `{local_ref.name}` on top of `{remote_ref.name}`."
            return CommandPlan(
                title=title,
                explanation=explanation,
                steps=[
                    CommandStep(["git", "switch", local_ref.name], f"Make `{local_ref.name}` the current branch."),
                    CommandStep(["git", "fetch", remote], f"Refresh `{remote}` remote-tracking references."),
                    CommandStep(["git", "rebase", remote_ref.name], f"Replay `{local_ref.name}` after `{remote_ref.name}`."),
                ],
                expected_effects=[
                    f"`{local_ref.name}` moves to rewritten commits after `{remote_ref.name}`.",
                    "Local commit hashes may change.",
                ],
                preview_steps=[
                    f"Fetch `{remote}`.",
                    f"Copy commits unique to `{local_ref.name}` after `{remote_ref.name}`.",
                    f"Move `{local_ref.name}` to the copied commits.",
                    "Old copied commits may become unreferenced.",
                ],
                warnings=["This rewrites local branch history."],
                history_rewrite=True,
                remote_impact="Reads from remote; does not write to it.",
                state_fingerprint=state_fingerprint(state),
            )
        else:
            raise ValueError("Unknown integration strategy.")
        return CommandPlan(
            title=title,
            explanation=explanation,
            steps=[
                CommandStep(["git", "switch", local_ref.name], f"Make `{local_ref.name}` the current branch."),
                CommandStep(["git", "fetch", remote], f"Refresh `{remote}` remote-tracking references."),
                CommandStep(merge_args, "Integrate the remote-tracking branch into the local branch."),
            ],
            expected_effects=effects,
            preview_steps=[
                f"Fetch `{remote}`.",
                f"Attach HEAD to `{local_ref.name}`.",
                f"Integrate `{remote_ref.name}` into `{local_ref.name}`.",
            ],
            remote_impact="Reads from remote; does not write to it.",
            state_fingerprint=state_fingerprint(state),
        )

    def integrate_local_branch(
        self,
        state: RepositoryState,
        source_ref: Reference,
        target_ref: Reference,
        strategy: str,
    ) -> CommandPlan:
        if source_ref.kind != "local_branch" or target_ref.kind != "local_branch":
            raise ValueError("Drag one local branch onto another local branch.")
        if strategy == "merge_source_into_target":
            return CommandPlan(
                title=f"Merge {source_ref.name} into {target_ref.name}",
                explanation=f"Switch to `{target_ref.name}` and merge `{source_ref.name}` into it.",
                steps=[
                    CommandStep(["git", "switch", target_ref.name], f"Make `{target_ref.name}` the current branch."),
                    CommandStep(["git", "merge", source_ref.name], f"Merge `{source_ref.name}` into `{target_ref.name}`."),
                ],
                expected_effects=[f"`{target_ref.name}` may move or receive a merge commit."],
                preview_steps=[
                    f"Attach HEAD to `{target_ref.name}`.",
                    f"Merge `{source_ref.name}` into `{target_ref.name}`.",
                    "Create a merge commit if fast-forward is not possible.",
                ],
                state_fingerprint=state_fingerprint(state),
            )
        if strategy == "rebase_source_onto_target":
            return CommandPlan(
                title=f"Rebase {source_ref.name} onto {target_ref.name}",
                explanation=f"Replay commits unique to `{source_ref.name}` on top of `{target_ref.name}`.",
                steps=[
                    CommandStep(["git", "switch", source_ref.name], f"Make `{source_ref.name}` the current branch."),
                    CommandStep(["git", "rebase", target_ref.name], f"Replay `{source_ref.name}` after `{target_ref.name}`."),
                ],
                expected_effects=[
                    f"`{source_ref.name}` moves to rewritten commits after `{target_ref.name}`.",
                    "Commit hashes on the rebased branch may change.",
                ],
                preview_steps=[
                    f"Attach HEAD to `{source_ref.name}`.",
                    f"Replay commits unique to `{source_ref.name}` after `{target_ref.name}`.",
                    f"Move `{source_ref.name}` to the rewritten commits.",
                ],
                warnings=["This rewrites local branch history."],
                history_rewrite=True,
                state_fingerprint=state_fingerprint(state),
            )
        raise ValueError("Unknown branch integration strategy.")

    def replay_commit_after(self, state: RepositoryState, source: Commit, target: Commit) -> CommandPlan:
        if source.oid == target.oid:
            raise ValueError("Drag a commit onto a different commit.")
        branch = f"gitualizer/replay-{source.short_oid}-after-{target.short_oid}"
        if any(ref.name == branch for ref in state.local_branches):
            branch = f"{branch}-new"
        return CommandPlan(
            title=f"Replay {source.short_oid} after {target.short_oid}",
            explanation=(
                "Create a new branch at the target commit, then cherry-pick the dragged commit onto it. "
                "This is a safe previewable way to construct the requested history shape without moving existing branches."
            ),
            steps=[
                CommandStep(["git", "switch", "-c", branch, target.oid], f"Create `{branch}` at the target commit."),
                CommandStep(["git", "cherry-pick", source.oid], "Replay the dragged commit onto the new branch."),
            ],
            expected_effects=[
                f"A new branch `{branch}` will be created at `{target.short_oid}`.",
                f"A new commit equivalent to `{source.short_oid}` may be created after `{target.short_oid}`.",
                "Existing branches are not moved by this plan.",
            ],
            preview_steps=[
                f"Create `{branch}` at `{target.short_oid}`.",
                f"Cherry-pick `{source.short_oid}` onto the new branch.",
                "Show conflicts if Git cannot apply the patch cleanly.",
            ],
            warnings=["Cherry-pick may stop for conflicts. If it does, Gitualizer will show the command failure and refreshed state."],
            state_fingerprint=state_fingerprint(state),
        )

    def create_branch_at_commit(self, state: RepositoryState, commit: Commit, branch: str) -> CommandPlan:
        branch = branch.strip()
        if not branch:
            raise ValueError("Enter a branch name.")
        if any(ref.name == branch and ref.kind == "local_branch" for ref in state.references):
            raise ValueError("A local branch with that name already exists.")
        return CommandPlan(
            title=f"Create branch {branch} at {commit.short_oid}",
            explanation=f"Create a new local branch label at commit `{commit.short_oid}`.",
            steps=[CommandStep(["git", "branch", branch, commit.oid], "Create the branch at the selected commit.")],
            expected_effects=[f"`{branch}` points to `{commit.short_oid}`.", "HEAD does not move."],
            preview_steps=[f"Attach a new branch label `{branch}` to commit `{commit.short_oid}`."],
            state_fingerprint=state_fingerprint(state),
        )

    def cherry_pick_commit_to_branch(self, state: RepositoryState, source: Commit, branch: Reference) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drop a commit onto a local branch.")
        return CommandPlan(
            title=f"Cherry-pick {source.short_oid} onto {branch.name}",
            explanation=f"Apply the change introduced by `{source.short_oid}` on top of `{branch.name}`.",
            steps=[
                CommandStep(["git", "switch", branch.name], f"Attach HEAD to `{branch.name}`."),
                CommandStep(["git", "cherry-pick", source.oid], f"Apply `{source.short_oid}`."),
            ],
            expected_effects=[f"`{branch.name}` may move to a new commit equivalent to `{source.short_oid}`."],
            preview_steps=[
                f"Attach HEAD to `{branch.name}`.",
                f"Create a new commit on `{branch.name}` with the patch from `{source.short_oid}`.",
            ],
            warnings=["Cherry-pick may stop for conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def revert_commit_on_branch(self, state: RepositoryState, source: Commit, branch: Reference) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drop a commit onto a local branch.")
        return CommandPlan(
            title=f"Revert {source.short_oid} on {branch.name}",
            explanation=f"Create a new commit on `{branch.name}` that undoes `{source.short_oid}`.",
            steps=[
                CommandStep(["git", "switch", branch.name], f"Attach HEAD to `{branch.name}`."),
                CommandStep(["git", "revert", "--no-edit", source.oid], f"Create a revert commit for `{source.short_oid}`."),
            ],
            expected_effects=[f"`{branch.name}` moves to a new revert commit."],
            preview_steps=[
                f"Attach HEAD to `{branch.name}`.",
                f"Create a new commit that applies the inverse patch of `{source.short_oid}`.",
            ],
            warnings=["Revert may stop for conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def revert_commit_on_current_branch(self, state: RepositoryState, source: Commit) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to revert a commit.")
        return self.revert_commit_on_branch(state, source, current)

    def drop_commit_from_current_branch(self, state: RepositoryState, source: Commit) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to drop a commit.")
        if len(source.parents) != 1:
            raise ValueError("Only single-parent commits can be dropped with this rebase plan.")
        parent = source.parents[0]
        return CommandPlan(
            title=f"Drop {source.short_oid} from {current.name}",
            explanation=(
                f"Rewrite `{current.name}` by replaying commits after `{source.short_oid}` onto its parent. "
                "This removes the selected commit from the branch history."
            ),
            steps=[
                CommandStep(["git", "switch", current.name], f"Attach HEAD to `{current.name}`."),
                CommandStep(
                    ["git", "rebase", "--onto", parent, source.oid, current.name],
                    f"Replay commits after `{source.short_oid}` onto its parent.",
                ),
            ],
            expected_effects=[
                f"`{source.short_oid}` is removed from `{current.name}` history if the rebase succeeds.",
                "Descendant commits are recreated with new hashes.",
            ],
            preview_steps=[
                f"Find the parent of `{source.short_oid}`.",
                f"Copy descendants of `{source.short_oid}` onto that parent.",
                f"Move `{current.name}` to the rewritten commits.",
            ],
            warnings=[
                "This rewrites branch history.",
                "If this branch was pushed, pushing afterward may require force-with-lease.",
            ],
            history_rewrite=True,
            state_fingerprint=state_fingerprint(state),
        )

    def reset_branch_to_commit(self, state: RepositoryState, branch: Reference, commit: Commit, mode: str) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drag a local branch onto a commit.")
        if mode not in {"soft", "mixed", "hard"}:
            raise ValueError("Unknown reset mode.")
        destructive = mode == "hard"
        return CommandPlan(
            title=f"Reset {branch.name} to {commit.short_oid} ({mode})",
            explanation=f"Move `{branch.name}` to `{commit.short_oid}` using `git reset --{mode}`.",
            steps=[
                CommandStep(["git", "switch", branch.name], f"Attach HEAD to `{branch.name}`."),
                CommandStep(["git", "reset", f"--{mode}", commit.oid], f"Move `{branch.name}` to `{commit.short_oid}`."),
            ],
            expected_effects=[f"`{branch.name}` points to `{commit.short_oid}`."],
            preview_steps=[
                f"Attach HEAD to `{branch.name}`.",
                f"Move the `{branch.name}` label to `{commit.short_oid}`.",
                _reset_mode_preview(mode),
            ],
            warnings=[_reset_mode_warning(mode)] if destructive else [_reset_mode_preview(mode)],
            destructive=destructive,
            history_rewrite=True,
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


def _reset_mode_preview(mode: str) -> str:
    if mode == "soft":
        return "Keep index and working tree changes staged."
    if mode == "mixed":
        return "Reset the index, but keep file contents in the working tree."
    return "Reset index and working tree to the target commit."


def _reset_mode_warning(mode: str) -> str:
    if mode == "hard":
        return "Hard reset discards uncommitted working tree and index changes."
    return _reset_mode_preview(mode)
