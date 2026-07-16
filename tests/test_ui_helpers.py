from pathlib import Path
from types import SimpleNamespace

from gitualizer.operations.command_plan import CommandPlan, CommandStep, ExecutionResult, StepResult
from gitualizer.ui import main_window
from gitualizer.ui.main_window import MainWindow, _looks_like_auth_failure, _render_diff_html


def test_diff_html_colors_added_removed_and_context_lines() -> None:
    rendered = _render_diff_html(" context\n-old <value>\n+new & value\n@@ -1 +1 @@")

    assert "#111111" in rendered
    assert "#cf222e" in rendered
    assert "#116329" in rendered
    assert "&lt;value&gt;" in rendered
    assert "&amp; value" in rendered
    assert "<value>" not in rendered


def test_auth_failure_detection_distinguishes_network_errors() -> None:
    assert _looks_like_auth_failure("git@github.com: Permission denied (publickey).")
    assert _looks_like_auth_failure("fatal: Authentication failed")
    assert not _looks_like_auth_failure("fatal: unable to access host: Network is unreachable")


def test_remote_plan_failure_updates_auth_after_execution(monkeypatch) -> None:
    plan = CommandPlan(
        "Push main",
        "",
        [CommandStep(["git", "push", "origin", "main"], "")],
        state_fingerprint="unchanged",
    )
    result = ExecutionResult(
        success=False,
        steps=[StepResult(plan.steps[0].args, 1, "", "rejected (non-fast-forward)")],
    )
    events = []
    alert = SimpleNamespace(
        hide=lambda: events.append("alert hidden"),
        deleteLater=lambda: events.append("alert deleted"),
    )
    status_bar = SimpleNamespace(showMessage=lambda *args: events.append(args[0]))
    window = SimpleNamespace(
        state=SimpleNamespace(path=Path("/repo")),
        reader=SimpleNamespace(read=lambda path: SimpleNamespace(path=path)),
        executor=SimpleNamespace(
            requires_terminal_auth=lambda command_plan: True,
            execute=lambda command_plan, path: events.append("executed") or result,
        ),
        _show_terminal_alert=lambda action: alert,
        _set_auth_status=lambda status: events.append(f"auth: {status}"),
        statusBar=lambda: status_bar,
        command_panel=SimpleNamespace(setHtml=lambda html: None),
        graph=SimpleNamespace(set_preview_plan=lambda preview: None),
        refresh=lambda: None,
    )
    monkeypatch.setattr(main_window, "state_fingerprint", lambda state: "unchanged")
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: None)

    MainWindow._execute_plan(window, plan)

    assert events.index("executed") < events.index("auth: unavailable")
    assert "Remote command failed; check the command result." in events


class FakeScrollBar:
    def __init__(self, value: int, maximum: int) -> None:
        self.current_value = value
        self.current_maximum = maximum

    def value(self) -> int:
        return self.current_value

    def maximum(self) -> int:
        return self.current_maximum

    def setValue(self, value: int) -> None:
        self.current_value = value


class FakeHistory:
    def __init__(self, scroll_bar: FakeScrollBar) -> None:
        self.scroll_bar = scroll_bar

    def verticalScrollBar(self) -> FakeScrollBar:
        return self.scroll_bar

    def setHtml(self, html: str) -> None:
        self.scroll_bar.current_maximum += 100
        self.scroll_bar.current_value = 0


def test_command_history_keeps_position_when_reader_scrolled_up() -> None:
    scroll_bar = FakeScrollBar(value=40, maximum=100)
    window = SimpleNamespace(
        command_history=FakeHistory(scroll_bar),
        command_history_events=[],
        show_command_stdout=SimpleNamespace(isChecked=lambda: False),
    )

    MainWindow._render_command_history(window)

    assert scroll_bar.value() == 40


def test_command_history_follows_updates_when_reader_is_at_bottom() -> None:
    scroll_bar = FakeScrollBar(value=100, maximum=100)
    window = SimpleNamespace(
        command_history=FakeHistory(scroll_bar),
        command_history_events=[],
        show_command_stdout=SimpleNamespace(isChecked=lambda: False),
    )

    MainWindow._render_command_history(window)

    assert scroll_bar.value() == 200
