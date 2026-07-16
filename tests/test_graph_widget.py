from pathlib import Path

from PySide6.QtWidgets import QApplication

from gitualizer.model.repository_state import Commit, HeadState, Reference, RepositoryState
from gitualizer.ui.graph_widget import CommitGraphWidget


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_local_remote_canvas_stays_wide_after_state_refresh() -> None:
    _application()
    widget = CommitGraphWidget()
    widget.set_mode("local_remote")

    widget.set_state(
        RepositoryState(
            path=Path("."),
            git_dir=Path(".git"),
            head=HeadState(None, None, None, False),
        )
    )

    assert widget.minimumWidth() == 656


def test_remote_card_mirrors_new_commits_missing_from_local_branch() -> None:
    _application()
    local = Reference(
        name="main",
        full_name="refs/heads/main",
        target="local",
        kind="local_branch",
        upstream="origin/main",
        ahead=1,
        behind=4,
    )
    remote = Reference(
        name="origin/main",
        full_name="refs/remotes/origin/main",
        target="remote",
        kind="remote_tracking",
    )
    widget = CommitGraphWidget()
    widget.set_state(
        RepositoryState(
            path=Path("."),
            git_dir=Path(".git"),
            head=HeadState(None, None, None, False),
            references=[local, remote],
        )
    )

    assert widget._comparison_counts(local) == (1, 4)
    assert widget._comparison_counts(remote) == (4, 1)


def test_context_selection_returns_all_selected_commits_only_when_clicked_inside_selection() -> None:
    _application()
    first = Commit("a" * 40, "a" * 12, tuple(), "A", "a@example.invalid", "2024-01-01", "first")
    second = Commit("b" * 40, "b" * 12, (first.oid,), "A", "a@example.invalid", "2024-01-02", "second")
    outside = Commit("c" * 40, "c" * 12, tuple(), "A", "a@example.invalid", "2024-01-03", "outside")
    widget = CommitGraphWidget()
    widget.set_state(
        RepositoryState(
            path=Path("."),
            git_dir=Path(".git"),
            head=HeadState(second.oid, second.short_oid, "main", False),
            commits={second.oid: second, first.oid: first, outside.oid: outside},
        )
    )
    widget._selected_oids = {first.oid, second.oid}

    assert {commit.oid for commit in widget.selected_commits(first)} == {first.oid, second.oid}
    assert widget.selected_commits(outside) == [outside]
