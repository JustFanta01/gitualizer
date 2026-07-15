from __future__ import annotations

from pathlib import Path

import pytest

from gitualizer.model.repository_state import Commit, FileChange, HeadState, Reference, RepositoryState
from gitualizer.operations.planner import OperationPlanner


def state() -> RepositoryState:
    return RepositoryState(
        path=Path("/repo"),
        git_dir=Path("/repo/.git"),
        head=HeadState(oid="a" * 40, short_oid="a" * 12, branch="main", detached=False),
        references=[
            Reference(
                name="main",
                full_name="refs/heads/main",
                target="a" * 40,
                kind="local_branch",
                upstream="origin/main",
                ahead=1,
                behind=0,
            ),
            Reference(name="feature", full_name="refs/heads/feature", target="b" * 40, kind="local_branch"),
        ],
        changes=[
            FileChange(path="edited.txt", area="working_tree", code="M"),
            FileChange(path="new.txt", area="untracked", code="??"),
            FileChange(path="staged.txt", area="staged", code="A"),
        ],
    )


def test_switch_branch_plan_uses_argument_array() -> None:
    plan = OperationPlanner().switch_branch(state(), "feature")

    assert plan.steps[0].args == ["git", "switch", "feature"]
    assert "git switch feature" in plan.commands_text


def test_switch_to_commit_uses_detached_head_plan() -> None:
    target = Commit("c" * 40, "c" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "target")

    plan = OperationPlanner().switch_to_commit(state(), target)

    assert plan.steps[0].args == ["git", "switch", "--detach", "c" * 40]
    assert "No branch label moves." in plan.expected_effects


def test_create_and_switch_branch_at_commit_uses_switch_c() -> None:
    target = Commit("c" * 40, "c" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "target")

    plan = OperationPlanner().create_and_switch_branch_at_commit(state(), target, "try-here")

    assert plan.steps[0].args == ["git", "switch", "-c", "try-here", "c" * 40]
    assert "HEAD attaches to `try-here`." in plan.expected_effects


def test_commit_requires_staged_changes_and_message() -> None:
    planner = OperationPlanner()

    with pytest.raises(ValueError):
        planner.commit(state(), "")

    plan = planner.commit(state(), "Add staged file")

    assert plan.steps[0].args == ["git", "commit", "-m", "Add staged file"]


def test_fetch_and_push_explain_remote_impact() -> None:
    planner = OperationPlanner()

    fetch = planner.fetch(state(), "origin")
    push = planner.push_current_branch(state())

    assert fetch.steps[0].args == ["git", "fetch", "origin"]
    assert push.steps[0].args == ["git", "push", "origin", "main"]
    assert "origin/main" in push.remote_impact


def test_drag_local_branch_onto_remote_tracking_pushes_to_remote_branch() -> None:
    repo_state = state()
    local = repo_state.local_branches[0]
    remote = Reference(
        name="origin/main",
        full_name="refs/remotes/origin/main",
        target="c" * 40,
        kind="remote_tracking",
    )

    plan = OperationPlanner().push_branch_to_remote_tracking(repo_state, local, remote)

    assert plan.steps[0].args == ["git", "push", "origin", "main"]
    assert "origin:main" in plan.expected_effects[0]
    assert "fast-forward" in plan.warnings[0]


def test_staging_selected_paths_keeps_paths_after_separator() -> None:
    plan = OperationPlanner().stage_paths(state(), [FileChange(path="file with spaces.txt", area="working_tree", code="M")])

    assert plan.steps[0].args == ["git", "add", "--", "file with spaces.txt"]
    assert "'file with spaces.txt'" in plan.commands_text


def test_drag_remote_tracking_onto_local_branch_offers_pull_strategy() -> None:
    repo_state = state()
    remote = Reference(
        name="origin/main",
        full_name="refs/remotes/origin/main",
        target="c" * 40,
        kind="remote_tracking",
    )
    local = repo_state.local_branches[0]

    plan = OperationPlanner().integrate_remote_tracking(repo_state, remote, local, "rebase")

    assert plan.steps[0].args == ["git", "switch", "main"]
    assert plan.steps[1].args == ["git", "fetch", "origin"]
    assert plan.steps[2].args == ["git", "rebase", "origin/main"]
    assert plan.graph_preview[0] == "Before:"
    assert "main" in "\n".join(plan.graph_preview)
    assert "origin/main" in "\n".join(plan.graph_preview)
    assert plan.history_rewrite is True


def test_drag_local_branch_onto_local_branch_can_merge() -> None:
    repo_state = state()
    main = repo_state.local_branches[0]
    feature = repo_state.local_branches[1]

    plan = OperationPlanner().integrate_local_branch(repo_state, feature, main, "merge_source_into_target")

    assert plan.steps[0].args == ["git", "switch", "main"]
    assert plan.steps[1].args == ["git", "merge", "feature"]
    assert "new merge commit on target branch" in "\n".join(plan.graph_preview)


def test_drag_staging_area_onto_branch_creates_commit_plan() -> None:
    plan = OperationPlanner().commit_to_branch(state(), "feature", "Save staged work")

    assert plan.steps[0].args == ["git", "switch", "feature"]
    assert plan.steps[1].args == ["git", "commit", "-m", "Save staged work"]
    assert "staging area" in plan.explanation


def test_drag_changes_to_trash_creates_destructive_discard_plan() -> None:
    plan = OperationPlanner().discard_changes(
        state(),
        [
            FileChange(path="edited.txt", area="working_tree", code="M"),
            FileChange(path="new.txt", area="untracked", code="??"),
        ],
    )

    assert plan.destructive is True
    assert plan.steps[0].args == ["git", "restore", "--staged", "--worktree", "--", "edited.txt"]
    assert plan.steps[1].args == ["git", "clean", "-f", "--", "new.txt"]


def test_drag_commit_onto_commit_creates_replay_branch_plan() -> None:
    repo_state = state()
    source = Commit("b" * 40, "b" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "source")
    target = Commit("c" * 40, "c" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "target")

    plan = OperationPlanner().replay_commit_after(repo_state, source, target)

    assert plan.steps[0].args == ["git", "switch", "-c", "gitualizer/replay-bbbbbbbbbbbb-after-cccccccccccc", "c" * 40]
    assert plan.steps[1].args == ["git", "cherry-pick", "b" * 40]
    assert plan.preview_steps


def test_drop_commit_on_branch_can_cherry_pick_or_revert() -> None:
    repo_state = state()
    source = Commit("b" * 40, "b" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "source")
    branch = repo_state.local_branches[0]

    cherry_pick = OperationPlanner().cherry_pick_commit_to_branch(repo_state, source, branch)
    revert = OperationPlanner().revert_commit_on_branch(repo_state, source, branch)

    assert cherry_pick.steps[1].args == ["git", "cherry-pick", "b" * 40]
    assert revert.steps[1].args == ["git", "revert", "--no-edit", "b" * 40]


def test_drop_branch_on_commit_can_reset_or_create_branch() -> None:
    repo_state = state()
    branch = repo_state.local_branches[0]
    target = Commit("c" * 40, "c" * 12, tuple(), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "target")

    reset = OperationPlanner().reset_branch_to_commit(repo_state, branch, target, "hard")
    create = OperationPlanner().create_branch_at_commit(repo_state, target, "recover-here")

    assert reset.destructive is True
    assert reset.history_rewrite is True
    assert reset.steps[1].args == ["git", "reset", "--hard", "c" * 40]
    assert create.steps[0].args == ["git", "branch", "recover-here", "c" * 40]


def test_commit_trash_can_revert_or_drop_from_current_branch() -> None:
    repo_state = state()
    source = Commit(
        "b" * 40,
        "b" * 12,
        ("a" * 40,),
        "A",
        "a@example.invalid",
        "2024-01-01T00:00:00+00:00",
        "source",
    )

    revert = OperationPlanner().revert_commit_on_current_branch(repo_state, source)
    drop = OperationPlanner().drop_commit_from_current_branch(repo_state, source)

    assert revert.steps[1].args == ["git", "revert", "--no-edit", "b" * 40]
    assert drop.steps[1].args == ["git", "rebase", "--onto", "a" * 40, "b" * 40, "main"]
    assert drop.history_rewrite is True


def test_graph_changing_plans_have_graph_previews() -> None:
    repo_state = state()
    planner = OperationPlanner()
    target = Commit("c" * 40, "c" * 12, ("a" * 40,), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "target")
    source = Commit("b" * 40, "b" * 12, ("a" * 40,), "A", "a@example.invalid", "2024-01-01T00:00:00+00:00", "source")
    remote = Reference(
        name="origin/main",
        full_name="refs/remotes/origin/main",
        target="c" * 40,
        kind="remote_tracking",
    )
    main = repo_state.local_branches[0]
    feature = repo_state.local_branches[1]

    graph_plans = [
        planner.switch_branch(repo_state, "feature"),
        planner.switch_to_commit(repo_state, target),
        planner.create_branch(repo_state, "new-branch"),
        planner.create_and_switch_branch_at_commit(repo_state, target, "try-here"),
        planner.commit(repo_state, "Save staged file"),
        planner.commit_to_branch(repo_state, "feature", "Save staged work"),
        planner.fetch(repo_state, "origin"),
        planner.fast_forward_current_branch(repo_state),
        planner.push_current_branch(repo_state),
        planner.push_branch_to_remote_tracking(repo_state, main, remote),
        planner.integrate_remote_tracking(repo_state, remote, main, "ff"),
        planner.integrate_remote_tracking(repo_state, remote, main, "merge"),
        planner.integrate_remote_tracking(repo_state, remote, main, "rebase"),
        planner.integrate_local_branch(repo_state, feature, main, "merge_source_into_target"),
        planner.integrate_local_branch(repo_state, feature, main, "rebase_source_onto_target"),
        planner.replay_commit_after(repo_state, source, target),
        planner.create_branch_at_commit(repo_state, target, "recover-here"),
        planner.cherry_pick_commit_to_branch(repo_state, source, main),
        planner.revert_commit_on_branch(repo_state, source, main),
        planner.drop_commit_from_current_branch(repo_state, source),
        planner.reset_branch_to_commit(repo_state, main, target, "mixed"),
    ]

    assert all(plan.graph_preview for plan in graph_plans)
    assert planner.stage_paths(repo_state, [FileChange(path="edited.txt", area="working_tree", code="M")]).graph_preview == []
