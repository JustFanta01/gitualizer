from __future__ import annotations

from pathlib import Path

import pytest

from gitualizer.model.repository_state import FileChange, HeadState, Reference, RepositoryState
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


def test_staging_selected_paths_keeps_paths_after_separator() -> None:
    plan = OperationPlanner().stage_paths(state(), [FileChange(path="file with spaces.txt", area="working_tree", code="M")])

    assert plan.steps[0].args == ["git", "add", "--", "file with spaces.txt"]
    assert "'file with spaces.txt'" in plan.commands_text
