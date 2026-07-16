from pathlib import Path

from PySide6.QtWidgets import QApplication

from gitualizer.git.runner import CommandEvent, GitResult
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
    assert window.auth_status_button.text() == "Auth: Authenticated."
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
    assert window.auth_state == "unavailable"
    assert "remains available offline" in window.statusBar().currentMessage()
    window.close()
    assert app is not None
