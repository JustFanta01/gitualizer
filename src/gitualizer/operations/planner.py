from __future__ import annotations

import hashlib
from typing import Optional

from gitualizer.model.repository_state import Commit, FileChange, Reference, RepositoryState, Stash
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
    for stash in state.stashes:
        parts.append(f"stash:{stash.ref}:{stash.oid}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


class OperationPlanner:
    def stash_paths(self, state: RepositoryState, changes: list[FileChange], message: str) -> CommandPlan:
        if any(change.area == "conflict" for change in changes):
            raise ValueError("Resolve conflicts before creating a stash.")
        message = message.strip()
        if not message:
            raise ValueError("Enter a name for the stash.")
        paths = _paths_for_changes(changes)
        if not paths:
            raise ValueError("Drag one or more working-tree files onto the stash panel.")
        args = ["git", "stash", "push"]
        if any(change.area == "untracked" for change in changes):
            args.append("--include-untracked")
        args.extend(["-m", message, "--", *paths])
        return CommandPlan(
            title=f"Stash {len(paths)} selected path(s)",
            explanation="Create a new stash containing the selected working-tree paths.",
            steps=[CommandStep(args, "Save the selected changes and restore those paths in the working tree.")],
            expected_effects=["A new stash entry is created.", "Selected changes are removed from the working tree."],
            preview_steps=["Collect the selected paths.", "Include untracked files when selected.", "Create one named stash entry."],
            warnings=["Ignored files are not included."],
            state_fingerprint=state_fingerprint(state),
        )

    def apply_stash_to_branch(self, state: RepositoryState, stash: Stash, branch: Reference) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drop a stash onto a local branch.")
        steps: list[CommandStep] = []
        if state.head.branch != branch.name:
            steps.append(CommandStep(["git", "switch", branch.name], f"Switch to `{branch.name}`."))
        steps.append(CommandStep(["git", "stash", "apply", stash.ref], f"Apply `{stash.ref}` without removing it."))
        return CommandPlan(
            title=f"Apply {stash.ref} to {branch.name}",
            explanation=f"Apply the stashed changes to `{branch.name}` and keep the stash for reuse.",
            steps=steps,
            expected_effects=[f"Stashed changes are applied to `{branch.name}`.", f"`{stash.ref}` remains in the stash list."],
            preview_steps=[f"Switch to `{branch.name}` if needed.", f"Apply `{stash.ref}` to the working tree and index."],
            warnings=["Applying a stash may stop with conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def apply_stash_to_working_tree(self, state: RepositoryState, stash: Stash) -> CommandPlan:
        target = state.head.branch or "the current detached HEAD"
        return CommandPlan(
            title=f"Apply {stash.ref} to working tree",
            explanation=f"Apply `{stash.ref}` to the working tree on {target} and keep the stash for reuse.",
            steps=[CommandStep(["git", "stash", "apply", stash.ref], f"Apply `{stash.ref}`.")],
            expected_effects=["Stashed changes are restored in the current working tree.", f"`{stash.ref}` remains in the stash list."],
            preview_steps=[f"Apply `{stash.ref}` without switching branches.", "Keep the stash entry."],
            warnings=["Applying a stash may stop with conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def drop_stash(self, state: RepositoryState, stash: Stash) -> CommandPlan:
        return CommandPlan(
            title=f"Drop {stash.ref}",
            explanation=f"Delete `{stash.ref}` from the repository without applying its changes.",
            steps=[CommandStep(["git", "stash", "drop", stash.ref], f"Delete `{stash.ref}`.")],
            expected_effects=[f"`{stash.ref}` is removed from the stash list.", "The working tree and current branch are unchanged."],
            preview_steps=[f"Delete the stash entry `{stash.ref}`.", "Do not apply its files."],
            warnings=["The stash may become unrecoverable after Git garbage collection."],
            destructive=True,
            state_fingerprint=state_fingerprint(state),
        )

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
            graph_preview=_head_move_graph_preview(state.head.branch or state.head.short_oid or "current", branch),
            state_fingerprint=state_fingerprint(state),
        )

    def switch_to_commit(self, state: RepositoryState, commit: Commit) -> CommandPlan:
        if state.head.oid == commit.oid and state.head.detached:
            raise ValueError("HEAD is already detached at that commit.")
        return CommandPlan(
            title=f"Switch to {commit.short_oid} detached",
            explanation=(
                f"Attach the working tree and HEAD directly to commit `{commit.short_oid}` without moving any branch. "
                "This is Git's detached HEAD mode."
            ),
            steps=[CommandStep(["git", "switch", "--detach", commit.oid], "Check out the selected commit without a branch.")],
            expected_effects=[
                f"HEAD points directly at `{commit.short_oid}`.",
                "No branch label moves.",
                "Git may refuse if local changes would be overwritten.",
            ],
            preview_steps=[
                f"Detach HEAD from `{state.head.branch or state.head.short_oid or 'current position'}`.",
                f"Place HEAD on commit `{commit.short_oid}`.",
                "Leave all branch labels where they are.",
            ],
            graph_preview=_detached_head_graph_preview(state.head.branch or state.head.short_oid or "current", commit.short_oid),
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
            graph_preview=_branch_label_graph_preview(state.head.branch or state.head.short_oid or "HEAD", branch, start),
            state_fingerprint=state_fingerprint(state),
        )

    def create_and_switch_branch_at_commit(self, state: RepositoryState, commit: Commit, branch: str) -> CommandPlan:
        branch = branch.strip()
        if not branch:
            raise ValueError("Enter a branch name.")
        if any(ref.name == branch and ref.kind == "local_branch" for ref in state.references):
            raise ValueError("A local branch with that name already exists.")
        return CommandPlan(
            title=f"Create and switch to {branch}",
            explanation=f"Create a new local branch named `{branch}` at commit `{commit.short_oid}` and make it current.",
            steps=[
                CommandStep(
                    ["git", "switch", "-c", branch, commit.oid],
                    "Create the branch at the selected commit and switch to it.",
                )
            ],
            expected_effects=[
                f"`{branch}` points to `{commit.short_oid}`.",
                f"HEAD attaches to `{branch}`.",
                "Git may refuse if local changes would be overwritten.",
            ],
            preview_steps=[
                f"Attach a new branch label `{branch}` to commit `{commit.short_oid}`.",
                f"Move HEAD to `{branch}`.",
            ],
            graph_preview=_branch_label_graph_preview(commit.short_oid, branch, commit.short_oid, head_moves=True),
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
            graph_preview=_new_commit_graph_preview(state.head.branch or "HEAD"),
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
            graph_preview=_new_commit_graph_preview(branch),
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
            graph_preview=_remote_tracking_graph_preview(f"{remote}/branch", "fetch"),
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
            graph_preview=_integration_graph_preview(current.name, current.upstream, "ff"),
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
            graph_preview=_push_graph_preview(current.name, current.upstream),
            warnings=["Git will reject this push if it is not a fast-forward."],
            remote_impact=f"May update `{current.upstream}` on `{remote}`.",
            state_fingerprint=state_fingerprint(state),
        )

    def push_branch_to_remote_tracking(
        self,
        state: RepositoryState,
        local_ref: Reference,
        remote_ref: Reference,
    ) -> CommandPlan:
        if local_ref.kind != "local_branch" or remote_ref.kind != "remote_tracking":
            raise ValueError("Drag a local branch onto a remote-tracking branch.")
        if "/" not in remote_ref.name:
            raise ValueError("Remote-tracking branch must include a remote name.")
        remote, remote_branch = remote_ref.name.split("/", 1)
        refspec = local_ref.name if local_ref.name == remote_branch else f"{local_ref.name}:{remote_branch}"
        return CommandPlan(
            title=f"Push {local_ref.name} to {remote_ref.name}",
            explanation=(
                f"Ask `{remote}` to move its `{remote_branch}` branch to the commit pointed to by local "
                f"`{local_ref.name}`. The local `{remote_ref.name}` tracking ref updates after a successful push/fetch."
            ),
            steps=[CommandStep(["git", "push", remote, refspec], "Push the local branch to the selected remote branch.")],
            expected_effects=[
                f"The remote branch `{remote}:{remote_branch}` may move to `{local_ref.name}`.",
                f"`{remote_ref.name}` may update after Git observes the successful push.",
            ],
            preview_steps=[
                f"Compare local `{local_ref.name}` with cached `{remote_ref.name}`.",
                f"If the remote accepts the update, move `{remote}:{remote_branch}` to `{local_ref.name}`.",
                f"Refresh the local remote-tracking label `{remote_ref.name}`.",
            ],
            graph_preview=_push_graph_preview(local_ref.name, remote_ref.name),
            warnings=[
                "Git will reject this push if it is not a fast-forward.",
                "If history was rewritten, use an explicit force-with-lease workflow instead of this normal push.",
            ],
            remote_impact=f"May update `{remote_branch}` on `{remote}`.",
            state_fingerprint=state_fingerprint(state),
        )

    def delete_local_branch(self, state: RepositoryState, branch: Reference, *, force: bool = False) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drag a local branch to the trash.")
        if branch.name == state.head.branch:
            raise ValueError("You cannot delete the currently checked out branch.")
        flag = "-D" if force else "-d"
        title_prefix = "Force delete" if force else "Delete"
        explanation = (
            f"Force delete the local branch label `{branch.name}`, even if Git does not consider it fully merged."
            if force
            else f"Delete the local branch label `{branch.name}`. Git will refuse if the branch is not fully merged."
        )
        warnings = [
            "FORCE DELETE: this removes the local branch label even when it contains unmerged work.",
            "The commits may become hard to find if no other ref points to them.",
            "This does not delete any remote branch.",
        ] if force else [
            "This deletes a branch label from your local repository.",
            "Git may refuse if the branch contains unmerged work; choose force delete only if that is intentional.",
        ]
        return CommandPlan(
            title=f"{title_prefix} local branch {branch.name}",
            explanation=explanation,
            steps=[CommandStep(["git", "branch", flag, branch.name], "Delete the selected local branch label.")],
            expected_effects=[
                f"The local branch label `{branch.name}` is removed.",
                "The commits remain in the repository if another ref or reflog still reaches them.",
            ],
            preview_steps=[
                f"Remove the local branch label `{branch.name}`.",
                "Do not contact any remote.",
                "Do not delete working tree files.",
            ],
            graph_preview=_delete_branch_graph_preview(branch.name, remote=False),
            warnings=warnings,
            destructive=True,
            state_fingerprint=state_fingerprint(state),
        )

    def delete_remote_branch(self, state: RepositoryState, branch: Reference) -> CommandPlan:
        if branch.kind != "remote_tracking":
            raise ValueError("Drag a remote-tracking branch to the trash.")
        if "/" not in branch.name:
            raise ValueError("Remote-tracking branch must include a remote name.")
        remote, remote_branch = branch.name.split("/", 1)
        if remote_branch == "HEAD":
            raise ValueError("Remote HEAD is a symbolic pointer and cannot be deleted as a branch.")
        return CommandPlan(
            title=f"DANGEROUS: Delete remote branch {branch.name}",
            explanation=(
                f"Ask remote `{remote}` to delete branch `{remote_branch}`. This affects the shared remote repository, "
                "not just your local cached remote-tracking label."
            ),
            steps=[
                CommandStep(["git", "push", remote, "--delete", remote_branch], "Delete the branch on the remote repository."),
                CommandStep(["git", "fetch", remote, "--prune"], "Refresh local remote-tracking refs after deletion."),
            ],
            expected_effects=[
                f"The remote branch `{remote}:{remote_branch}` is deleted if the remote accepts the request.",
                f"The local cached label `{branch.name}` disappears after pruning.",
            ],
            preview_steps=[
                f"Send a delete request to remote `{remote}` for branch `{remote_branch}`.",
                f"Prune the cached remote-tracking label `{branch.name}`.",
                "Local branches are not deleted by this plan.",
            ],
            graph_preview=_delete_branch_graph_preview(branch.name, remote=True),
            warnings=[
                "DANGEROUS REMOTE OPERATION: this can delete a branch for everyone using that remote.",
                "This is not the same as deleting a local branch label.",
                "Make sure no one still needs the remote branch before executing.",
            ],
            destructive=True,
            remote_impact=f"Deletes `{remote_branch}` on `{remote}`.",
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
                graph_preview=_integration_graph_preview(local_ref.name, remote_ref.name, "rebase"),
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
            graph_preview=_integration_graph_preview(local_ref.name, remote_ref.name, strategy),
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
                graph_preview=_integration_graph_preview(target_ref.name, source_ref.name, "merge"),
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
                graph_preview=_integration_graph_preview(source_ref.name, target_ref.name, "rebase"),
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
            graph_preview=_replay_commit_graph_preview(source.short_oid, target.short_oid, branch),
            warnings=["Cherry-pick may stop for conflicts. If it does, Gitualizer will show the command failure and refreshed state."],
            state_fingerprint=state_fingerprint(state),
        )

    def replay_commits_after(self, state: RepositoryState, sources: list[Commit], target: Commit) -> CommandPlan:
        ordered = _oldest_first(state, sources)
        if not ordered:
            raise ValueError("Select one or more commits.")
        if any(commit.oid == target.oid for commit in ordered):
            raise ValueError("Drop the selection onto a commit outside the selection.")
        branch = f"gitualizer/replay-{len(ordered)}-after-{target.short_oid}"
        if any(ref.name == branch for ref in state.local_branches):
            branch += "-new"
        labels = ", ".join(commit.short_oid for commit in ordered)
        return CommandPlan(
            title=f"Replay {len(ordered)} commits after {target.short_oid}",
            explanation=f"Create `{branch}` at the target and replay the selected commits in chronological order.",
            steps=[
                CommandStep(["git", "switch", "-c", branch, target.oid], f"Create `{branch}` at the target."),
                CommandStep(["git", "cherry-pick", *[commit.oid for commit in ordered]], f"Replay {len(ordered)} selected commits."),
            ],
            expected_effects=[f"A new branch `{branch}` contains copies of the selected commits after `{target.short_oid}`."],
            preview_steps=[f"Create `{branch}` at `{target.short_oid}`.", f"Cherry-pick, oldest first: {labels}."],
            warnings=["Cherry-pick may stop for conflicts."],
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
            graph_preview=_branch_label_graph_preview(commit.short_oid, branch, commit.short_oid),
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
            graph_preview=_copy_commit_to_branch_graph_preview(source.short_oid, branch.name, "cherry-pick"),
            warnings=["Cherry-pick may stop for conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def cherry_pick_commits_to_branch(self, state: RepositoryState, sources: list[Commit], branch: Reference) -> CommandPlan:
        if branch.kind != "local_branch":
            raise ValueError("Drop commits onto a local branch.")
        ordered = _oldest_first(state, sources)
        if not ordered:
            raise ValueError("Select one or more commits.")
        return CommandPlan(
            title=f"Cherry-pick {len(ordered)} commits onto {branch.name}",
            explanation=f"Apply the selected commits to `{branch.name}` in chronological order.",
            steps=[
                CommandStep(["git", "switch", branch.name], f"Attach HEAD to `{branch.name}`."),
                CommandStep(["git", "cherry-pick", *[commit.oid for commit in ordered]], "Apply the selected commits."),
            ],
            expected_effects=[f"`{branch.name}` moves forward by up to {len(ordered)} new commits."],
            preview_steps=[f"Attach HEAD to `{branch.name}`.", "Replay the selected commits from oldest to newest."],
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
            graph_preview=_copy_commit_to_branch_graph_preview(source.short_oid, branch.name, "revert"),
            warnings=["Revert may stop for conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def revert_commit_on_current_branch(self, state: RepositoryState, source: Commit) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to revert a commit.")
        return self.revert_commit_on_branch(state, source, current)

    def revert_commits_on_current_branch(self, state: RepositoryState, sources: list[Commit]) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to revert commits.")
        ordered = _oldest_first(state, sources)
        if not ordered:
            raise ValueError("Select one or more commits.")
        if any(len(commit.parents) != 1 for commit in ordered):
            raise ValueError("Bulk revert currently supports only single-parent commits.")
        newest_first = list(reversed(ordered))
        return CommandPlan(
            title=f"Revert {len(newest_first)} commits on {current.name}",
            explanation=(
                f"Create {len(newest_first)} new commits on `{current.name}` that undo the selected changes "
                "without rewriting existing history."
            ),
            steps=[
                CommandStep(["git", "switch", current.name], f"Attach HEAD to `{current.name}`."),
                CommandStep(
                    ["git", "revert", "--no-edit", *[commit.oid for commit in newest_first]],
                    "Revert the selected commits from newest to oldest.",
                ),
            ],
            expected_effects=[
                f"`{current.name}` moves forward by up to {len(newest_first)} revert commits.",
                "The selected commits remain in history.",
            ],
            preview_steps=[
                f"Attach HEAD to `{current.name}`.",
                "Apply inverse patches from newest selected commit to oldest.",
                "Create one revert commit for each selected commit.",
            ],
            warnings=["Revert may stop for conflicts."],
            state_fingerprint=state_fingerprint(state),
        )

    def drop_commit_from_current_branch(self, state: RepositoryState, source: Commit) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to drop a commit.")
        if source.oid not in _reachable_from(state, current.target):
            raise ValueError(f"{source.short_oid} does not belong to the current branch `{current.name}`.")
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
            graph_preview=_drop_commit_graph_preview(current.name, source.short_oid),
            warnings=[
                "This rewrites branch history.",
                "If this branch was pushed, pushing afterward may require force-with-lease.",
            ],
            history_rewrite=True,
            state_fingerprint=state_fingerprint(state),
        )

    def drop_commits_from_current_branch(self, state: RepositoryState, sources: list[Commit]) -> CommandPlan:
        current = _current_branch_ref(state)
        if current is None:
            raise ValueError("A local branch must be checked out to drop commits.")
        ordered = _oldest_first(state, sources)
        if not ordered or any(len(commit.parents) != 1 for commit in ordered):
            raise ValueError("Select a sequence of single-parent commits.")
        branch_history = _reachable_from(state, current.target)
        if any(commit.oid not in branch_history for commit in ordered):
            raise ValueError(f"The selection does not belong to the current branch `{current.name}`.")
        selected = {commit.oid for commit in ordered}
        for older, newer in zip(ordered, ordered[1:]):
            if older.oid not in newer.parents:
                raise ValueError("The selected commits must form one contiguous sequence.")
        oldest, newest = ordered[0], ordered[-1]
        return CommandPlan(
            title=f"Drop {len(ordered)} commits from {current.name}",
            explanation=f"Rewrite `{current.name}` while omitting the selected contiguous commit sequence.",
            steps=[
                CommandStep(["git", "switch", current.name], f"Attach HEAD to `{current.name}`."),
                CommandStep(["git", "rebase", "--onto", oldest.parents[0], newest.oid, current.name], "Replay descendants after the removed sequence."),
            ],
            expected_effects=[f"{len(selected)} selected commits are removed from `{current.name}` history."],
            preview_steps=["Keep the parent before the selection.", "Omit the selected sequence.", "Replay later descendants."],
            warnings=["This rewrites branch history and may require force-with-lease after a previous push."],
            history_rewrite=True,
            destructive=True,
            state_fingerprint=state_fingerprint(state),
        )

    def forget_unreachable_commits(self, state: RepositoryState, sources: list[Commit]) -> CommandPlan:
        if not sources:
            raise ValueError("Select one or more unreachable commits.")
        reachable: set[str] = set()
        for ref in state.references:
            reachable.update(_reachable_from(state, ref.target))
        if state.head.oid:
            reachable.update(_reachable_from(state, state.head.oid))
        if any(commit.oid in reachable for commit in sources):
            raise ValueError("Only commits not reachable from a branch, tag, or HEAD can be forgotten.")
        return CommandPlan(
            title="Forget all unreachable commits from reflogs",
            explanation=(
                "Expire repository-wide reflog entries for commits that are no longer reachable. "
                "The selected lost commits will disappear from Gitualizer and become eligible for Git garbage collection."
            ),
            steps=[
                CommandStep(
                    ["git", "reflog", "expire", "--expire-unreachable=now", "--all"],
                    "Expire all unreachable entries from every reflog in this repository.",
                )
            ],
            expected_effects=[
                "All currently unreachable commits disappear from reflog-based history views.",
                "Git may permanently remove their objects during a later automatic or manual garbage collection.",
            ],
            preview_steps=[
                "Find reflog entries that are unreachable from current refs.",
                "Expire those entries across the repository.",
                "Leave every branch, tag, HEAD, index, and working-tree file unchanged.",
            ],
            graph_preview=[
                "Before:  main o---o       x---x  lost reflog-only commits",
                "After:   main o---o              lost sequence no longer shown",
            ],
            warnings=[
                "REPOSITORY-WIDE: this affects every unreachable reflog entry, not only the selected commits.",
                "You lose the normal reflog recovery path for abandoned resets, rebases, and deleted branches.",
                "This cannot be reliably undone after Git garbage-collects the underlying objects.",
            ],
            destructive=True,
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
            graph_preview=_reset_branch_graph_preview(branch.name, commit.short_oid, mode),
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


def _oldest_first(state: RepositoryState, commits: list[Commit]) -> list[Commit]:
    selected = {commit.oid for commit in commits}
    # Repository commits are date-ordered newest first.
    return [commit for commit in reversed(list(state.commits.values())) if commit.oid in selected]


def _reachable_from(state: RepositoryState, tip: str) -> set[str]:
    reachable: set[str] = set()
    pending = [tip]
    while pending:
        oid = pending.pop()
        if oid in reachable:
            continue
        reachable.add(oid)
        commit = state.commits.get(oid)
        if commit is not None:
            pending.extend(commit.parents)
    return reachable


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


def _head_move_graph_preview(before: str, after: str) -> list[str]:
    return [
        "Before:",
        f"  o---A  {before}, HEAD",
        f"  o---B  {after}",
        "",
        "After:",
        f"  o---A  {before}",
        f"  o---B  {after}, HEAD",
        "       ^ highlighted: HEAD attaches to this branch",
    ]


def _detached_head_graph_preview(before: str, commit: str) -> list[str]:
    return [
        "Before:",
        f"  o---A  {before}, HEAD",
        f"       \\",
        f"        B  {commit}",
        "",
        "After:",
        f"  o---A  {before}",
        f"       \\",
        f"        B  HEAD detached at {commit}",
        "        ^ highlighted: HEAD points directly at this commit",
    ]


def _branch_label_graph_preview(anchor: str, branch: str, commit: str, *, head_moves: bool = False) -> list[str]:
    head = ", HEAD" if head_moves else ""
    return [
        "Before:",
        f"  o---A  {anchor}",
        "",
        "After:",
        f"  o---A  {anchor}, {branch}{head}",
        f"       ^ highlighted: new branch label at {commit}",
    ]


def _new_commit_graph_preview(branch: str) -> list[str]:
    return [
        "Before:",
        f"  o---A  {branch}, HEAD",
        "",
        "After:",
        f"  o---A---N  {branch}, HEAD",
        "          ^ highlighted: new commit from the index",
    ]


def _remote_tracking_graph_preview(remote_ref: str, action: str) -> list[str]:
    return [
        "Before:",
        f"  o---A  {remote_ref}",
        "",
        "After:",
        f"  o---A---B  {remote_ref}",
        f"          ^ highlighted: {action} may move this remote-tracking label",
    ]


def _push_graph_preview(local_branch: str, remote_ref: str) -> list[str]:
    return [
        "Before:",
        f"  o---A  {remote_ref}",
        f"       \\",
        f"        B  {local_branch}",
        "",
        "After:",
        f"  o---A---B  {local_branch}, {remote_ref}",
        "          ^ highlighted: remote branch catches up to local branch",
    ]


def _delete_branch_graph_preview(branch: str, *, remote: bool) -> list[str]:
    scope = "remote branch" if remote else "local branch label"
    return [
        "Before:",
        f"  o---A---B  {branch}",
        "",
        "After:",
        "  o---A---B",
        f"          xxx highlighted: deleted {scope} `{branch}`",
    ]


def _integration_graph_preview(target_branch: str, source_branch: str, strategy: str) -> list[str]:
    if strategy == "ff":
        return [
            "Before:",
            f"  o---A  {target_branch}",
            f"       \\",
            f"        B---C  {source_branch}",
            "",
            "After:",
            f"  o---A---B---C  {target_branch}, {source_branch}",
            "          ^^^^^ highlighted: target branch label moves forward",
        ]
    if strategy == "merge":
        return [
            "Before:",
            f"  o---A  {target_branch}",
            f"       \\",
            f"        B---C  {source_branch}",
            "",
            "After:",
            f"  o---A-------M  {target_branch}",
            f"       \\     /",
            f"        B---C  {source_branch}",
            "          ^^^ highlighted: new merge commit on target branch",
        ]
    if strategy == "rebase":
        return [
            "Before:",
            f"  o---A  {source_branch}",
            f"       \\",
            f"        B---C  {target_branch}",
            "",
            "After:",
            f"  o---B---C---A'  {source_branch}",
            f"      {target_branch}",
            "          ^^^^^ highlighted: source branch is replayed and rewritten",
        ]
    return []


def _replay_commit_graph_preview(source_commit: str, target_commit: str, branch: str) -> list[str]:
    return [
        "Before:",
        f"  o---A---{target_commit}",
        f"  o---B---{source_commit}",
        "",
        "After:",
        f"  o---A---{target_commit}---N  {branch}, HEAD",
        f"                 ^ highlighted: copied change from {source_commit}",
    ]


def _copy_commit_to_branch_graph_preview(source_commit: str, branch: str, action: str) -> list[str]:
    label = "copied change" if action == "cherry-pick" else "revert commit"
    return [
        "Before:",
        f"  o---A  {branch}, HEAD",
        f"  o---B  {source_commit}",
        "",
        "After:",
        f"  o---A---N  {branch}, HEAD",
        f"          ^ highlighted: new {label} for {source_commit}",
    ]


def _drop_commit_graph_preview(branch: str, commit: str) -> list[str]:
    return [
        "Before:",
        f"  o---A---{commit}---C  {branch}, HEAD",
        "",
        "After:",
        f"  o---A---C'  {branch}, HEAD",
        f"      xxx highlighted: {commit} is removed and descendants are rewritten",
    ]


def _reset_branch_graph_preview(branch: str, commit: str, mode: str) -> list[str]:
    return [
        "Before:",
        f"  o---A---B  {branch}, HEAD",
        f"      {commit}",
        "",
        "After:",
        f"  o---A  {branch}, HEAD",
        f"      ^ highlighted: branch label moves to {commit}",
        f"      working tree/index behavior: reset --{mode}",
    ]
