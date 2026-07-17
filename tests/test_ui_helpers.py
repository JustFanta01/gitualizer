from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from gitualizer.git.runner import CommandEvent, GitResult
from gitualizer.operations.command_plan import ExecutionResult, StepResult
from gitualizer.ui import main_window
from gitualizer.ui.main_window import MainWindow, _looks_like_auth_failure, _render_diff_html


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_diff_html_colors_added_removed_and_context_lines() -> None:
    rendered = _render_diff_html(" context\n-old <value>\n+new & value\n@@ -1 +1 @@")

    assert "#111111" in rendered
    assert "#cf222e" in rendered
    assert "#116329" in rendered
    assert "&lt;value&gt;" in rendered
    assert "&amp; value" in rendered
    assert "<value>" not in rendered


def test_command_history_is_lightweight_and_only_follows_at_bottom() -> None:
    app = _application()
    window = MainWindow()
    window.resize(900, 500)
    window.show()

    for index in range(80):
        window._append_command_event(
            CommandEvent(
                phase="finished",
                command=("git", "status", f"item-{index}"),
                cwd=None,
                interactive=False,
                timestamp="12:34:56",
                returncode=0,
                stdout="x" * 100_000,
                stderr="y" * 100_000,
            )
        )
    app.processEvents()

    history = window.command_history
    scroll_bar = history.verticalScrollBar()
    assert scroll_bar.value() == scroll_bar.maximum()
    assert "[12:34:56] $ git status item-79 exit 0" in history.toPlainText()
    assert "xxx" not in history.toPlainText()
    assert "yyy" not in history.toPlainText()
    rendered_html = history.toHtml()
    assert "#0969da" in rendered_html
    assert "#1a7f37" in rendered_html

    scroll_bar.setValue(max(0, scroll_bar.maximum() // 2))
    app.processEvents()
    previous_value = scroll_bar.value()
    window._append_command_event(
        CommandEvent("finished", ("git", "fetch"), None, True, "12:35:00", returncode=1)
    )
    app.processEvents()
    assert scroll_bar.value() == previous_value

    scroll_bar.setValue(scroll_bar.maximum())
    window._append_command_event(
        CommandEvent("finished", ("git", "push"), None, True, "12:35:01", returncode=1)
    )
    app.processEvents()
    assert scroll_bar.value() == scroll_bar.maximum()
    window.close()


def test_auth_status_tracks_remote_commands_and_elapsed_time(monkeypatch) -> None:
    app = _application()
    window = MainWindow()
    now = [100.0]
    monkeypatch.setattr(main_window.time, "monotonic", lambda: now[0])

    window._append_command_event(
        CommandEvent("started", ("git", "fetch"), None, True, "12:00:00")
    )
    assert window.auth_state == "required"

    window._append_command_event(
        CommandEvent("finished", ("git", "fetch"), None, True, "12:00:01", returncode=0)
    )
    now[0] = 134.0
    window._update_auth_status_label()

    assert window.auth_state == "authenticated"
    assert window.auth_status_button.text() == "Remote auth: Authenticated."
    details = window._auth_details_html()
    assert "Last terminal authentication command:</span> 34 sec ago" in details
    assert "Last command using remote authentication:</span> 34 sec ago" in details
    assert "#0969da" in details
    assert "#8250df" in details
    window.close()
    assert app is not None


def test_auth_failure_detection_distinguishes_network_errors() -> None:
    assert _looks_like_auth_failure("Permission denied (publickey).")
    assert _looks_like_auth_failure("fatal: Authentication failed")
    assert not _looks_like_auth_failure("Network is unreachable")


def test_fetch_timeout_returns_ui_to_offline_mode() -> None:
    app = _application()
    window = MainWindow()
    window.fetch_in_progress = True

    window._finish_fetch(
        GitResult(["git", "fetch"], Path("/repo"), "", "timed out", 124),
        interactive=False,
    )

    assert not window.fetch_in_progress
    assert window.network_state == "offline"
    assert window.auth_state == "unavailable"
    assert "remains available offline" in window.statusBar().currentMessage()
    window.close()
    assert app is not None


def test_auto_fetch_auth_failure_reopens_terminal_prompt() -> None:
    app = _application()
    window = MainWindow()
    window.fetch_in_progress = True
    retries = []
    window._start_fetch = lambda *, interactive: retries.append(interactive)

    window._finish_fetch(
        GitResult(["git", "fetch"], Path("/repo"), "", "fatal: Authentication failed", 128),
        interactive=False,
    )

    assert not window.fetch_in_progress
    assert window.network_state == "online"
    assert window.auth_state == "required"
    assert retries == [True]
    assert "opening terminal prompt" in window.statusBar().currentMessage()
    window.close()
    assert app is not None


def test_interactive_fetch_cancel_keeps_offline_with_required_auth() -> None:
    app = _application()
    window = MainWindow()
    window.fetch_in_progress = True

    window._finish_fetch(
        GitResult(["git", "fetch"], Path("/repo"), "", "Command interrupted.", 130),
        interactive=True,
    )

    assert not window.fetch_in_progress
    assert window.network_state == "unknown"
    assert window.auth_state == "required"
    assert "exit 130" in window.statusBar().currentMessage()
    assert "remains available offline" in window.statusBar().currentMessage()
    window.close()
    assert app is not None


def test_interactive_fetch_auth_refusal_keeps_network_unknown() -> None:
    app = _application()
    window = MainWindow()
    window.fetch_in_progress = True

    window._finish_fetch(
        GitResult(["git", "fetch"], Path("/repo"), "", "", 128),
        interactive=True,
    )

    assert not window.fetch_in_progress
    assert window.network_state == "unknown"
    assert window.auth_state == "required"
    assert "exit 128" in window.statusBar().currentMessage()
    assert "remains available offline" in window.statusBar().currentMessage()
    window.close()
    assert app is not None


def test_successful_fetch_marks_network_online_and_remote_auth_authenticated() -> None:
    app = _application()
    window = MainWindow()
    window.fetch_in_progress = True

    window._finish_fetch(
        GitResult(["git", "fetch"], Path("/repo"), "", "", 0),
        interactive=True,
    )

    assert not window.fetch_in_progress
    assert window.network_state == "online"
    assert window.network_status_button.text() == "Network: Online."
    assert window.auth_state == "authenticated"
    assert window.auth_status_button.text() == "Remote auth: Authenticated."
    window.close()
    assert app is not None


def test_auth_alert_is_closable() -> None:
    app = _application()
    window = MainWindow()
    alert = window._create_auth_alert(show_details=False)

    assert alert.standardButtons() & QMessageBox.StandardButton.Close
    window.close()
    assert app is not None


def test_failed_remote_operation_updates_network_offline_immediately() -> None:
    app = _application()
    window = MainWindow()

    window._update_remote_status_from_execution(
        ExecutionResult(
            success=False,
            steps=[
                StepResult(
                    ["git", "push", "origin", "main"],
                    128,
                    "",
                    "ssh: connect to host example.invalid port 22: Network is unreachable",
                )
            ],
        ),
        Path("/repo"),
    )

    assert window.network_state == "offline"
    assert window.auth_state == "unavailable"
    assert "Remote unavailable" in window.statusBar().currentMessage()
    window.close()
    assert app is not None


def test_failed_remote_operation_probes_when_interactive_stderr_is_not_captured() -> None:
    app = _application()
    window = MainWindow()
    calls = []

    def fake_run(args, cwd=None, **kwargs):
        calls.append((args, cwd, kwargs))
        return GitResult(["git", *args], cwd, "", "fatal: Authentication failed", 128)

    window.reader.runner.run = fake_run

    window._update_remote_status_from_execution(
        ExecutionResult(
            success=False,
            steps=[StepResult(["git", "push", "origin", "main"], 128, "", "")],
        ),
        Path("/repo"),
    )

    assert calls
    assert calls[0][0] == ["ls-remote", "--heads", "origin"]
    assert calls[0][2]["timeout"] == main_window.FETCH_NONINTERACTIVE_TIMEOUT_SECONDS
    assert window.network_state == "online"
    assert window.auth_state == "required"
    assert "Remote authentication required" in window.statusBar().currentMessage()
    window.close()
    assert app is not None
