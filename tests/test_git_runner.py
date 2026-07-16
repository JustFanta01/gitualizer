from pathlib import Path
import subprocess

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
    assert invocation["kwargs"] == {"cwd": tmp_path.resolve(), "shell": False}
    assert [event.phase for event in events] == ["started", "finished"]
    assert all(event.interactive for event in events)
    assert events[-1].returncode == 0


def test_remote_auth_environment_uses_five_minute_external_sessions() -> None:
    interactive = remote_auth_environment(interactive=True)
    background = remote_auth_environment(interactive=False)

    assert interactive["GIT_CONFIG_VALUE_1"] == f"cache --timeout={AUTH_SESSION_SECONDS}"
    assert f"ControlPersist={AUTH_SESSION_SECONDS}" in interactive["GIT_SSH_COMMAND"]
    assert "ConnectTimeout=10" in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" not in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in background["GIT_SSH_COMMAND"]
    assert background["GIT_CONFIG_VALUE_3"] == "10"
