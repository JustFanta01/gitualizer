import os
from pathlib import Path
import subprocess

from gitualizer.git import runner as runner_module
from gitualizer.git.runner import AUTH_SESSION_SECONDS, GitRunner, remote_auth_environment


def test_interactive_run_inherits_authentication_channels(monkeypatch, tmp_path: Path) -> None:
    invocation = {}
    events = []

    def fake_run(command, **kwargs):
        invocation["command"] = command
        invocation["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = GitRunner()
    runner.set_observer(events.append)
    result = runner.run_interactive(["fetch", "--all"], cwd=tmp_path)

    assert result.returncode == 0
    assert invocation["command"] == ["git", "fetch", "--all"]
    assert invocation["kwargs"]["cwd"] == tmp_path.resolve()
    assert invocation["kwargs"]["shell"] is False
    assert invocation["kwargs"]["timeout"] == 300
    assert "stdin" not in invocation["kwargs"]
    assert "stdout" not in invocation["kwargs"]
    assert "stderr" not in invocation["kwargs"]
    if os.name == "posix":
        assert callable(invocation["kwargs"]["preexec_fn"])
    assert [event.phase for event in events] == ["started", "finished"]
    assert all(event.interactive for event in events)
    assert events[-1].returncode == 0


def test_interactive_interrupt_is_reported_as_failed_command(monkeypatch, tmp_path: Path) -> None:
    events = []

    def fake_run(command, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = GitRunner()
    runner.set_observer(events.append)
    result = runner.run_interactive(["fetch", "--all"], cwd=tmp_path)

    assert result.returncode == 130
    assert [event.phase for event in events] == ["started", "finished"]
    assert events[-1].interactive
    assert events[-1].returncode == 130


def test_interactive_timeout_is_reported_as_failed_command(monkeypatch, tmp_path: Path) -> None:
    events = []

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = GitRunner()
    runner.set_observer(events.append)
    result = runner.run_interactive(["fetch", "--all"], cwd=tmp_path, timeout=7)

    assert result.returncode == 124
    assert result.stderr == "Command timed out after 7 seconds."
    assert [event.phase for event in events] == ["started", "finished"]
    assert events[-1].interactive
    assert events[-1].returncode == 124


def test_terminal_interrupt_callback_runs_when_no_interactive_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runner_module, "_active_interactive_commands", 0)
    monkeypatch.setattr(runner_module, "_terminal_interrupt_callback", lambda: calls.append("quit"))

    runner_module._handle_terminal_interrupt(2, None)

    assert calls == ["quit"]


def test_remote_auth_environment_uses_five_minute_external_sessions() -> None:
    interactive = remote_auth_environment(interactive=True)
    background = remote_auth_environment(interactive=False)

    assert interactive["GIT_CONFIG_VALUE_1"] == f"cache --timeout={AUTH_SESSION_SECONDS}"
    assert f"ControlPersist={AUTH_SESSION_SECONDS}" in interactive["GIT_SSH_COMMAND"]
    assert "ConnectTimeout=10" in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" not in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in background["GIT_SSH_COMMAND"]
    assert background["GIT_CONFIG_VALUE_3"] == "10"
