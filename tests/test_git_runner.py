from pathlib import Path
from io import StringIO
import subprocess

from gitualizer.git.runner import AUTH_SESSION_SECONDS, GitRunner, remote_auth_environment


def test_interactive_run_inherits_authentication_channels(monkeypatch, tmp_path: Path) -> None:
    invocation = {}
    events = []

    class FakeProcess:
        stdout = StringIO("remote output\n")
        stderr = StringIO("remote warning\n")

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        invocation["command"] = command
        invocation["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    runner = GitRunner()
    runner.set_observer(events.append)
    result = runner.run_interactive(["fetch", "--all"], cwd=tmp_path)

    assert result.returncode == 0
    assert invocation["command"] == ["git", "fetch", "--all"]
    assert invocation["kwargs"] == {
        "cwd": tmp_path.resolve(),
        "shell": False,
        "stdin": None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    assert [event.phase for event in events] == ["started", "finished"]
    assert all(event.interactive for event in events)
    assert events[-1].returncode == 0
    assert result.stdout == "remote output\n"
    assert result.stderr == "remote warning\n"
    assert events[-1].stdout == result.stdout
    assert events[-1].stderr == result.stderr


def test_remote_auth_environment_uses_five_minute_external_sessions() -> None:
    interactive = remote_auth_environment(interactive=True)
    background = remote_auth_environment(interactive=False)

    assert interactive["GIT_CONFIG_VALUE_1"] == f"cache --timeout={AUTH_SESSION_SECONDS}"
    assert f"ControlPersist={AUTH_SESSION_SECONDS}" in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" not in interactive["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in background["GIT_SSH_COMMAND"]


def test_interactive_run_returns_timeout_without_blocking_forever(monkeypatch, tmp_path: Path) -> None:
    class TimedOutProcess:
        stdout = StringIO()
        stderr = StringIO()

        def __init__(self):
            self.killed = False

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired(["git", "fetch"], timeout)
            return -9

        def kill(self):
            self.killed = True

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: TimedOutProcess())

    result = GitRunner().run_interactive(["fetch"], cwd=tmp_path, timeout=2)

    assert result.returncode == 124
    assert "timed out" in result.stderr
