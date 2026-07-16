from pathlib import Path
import subprocess

from gitualizer.git.runner import GitRunner


def test_interactive_run_inherits_authentication_channels(monkeypatch, tmp_path: Path) -> None:
    invocation = {}

    def fake_run(command, **kwargs):
        invocation["command"] = command
        invocation["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = GitRunner().run_interactive(["fetch", "--all"], cwd=tmp_path)

    assert result.returncode == 0
    assert invocation["command"] == ["git", "fetch", "--all"]
    assert invocation["kwargs"] == {"cwd": tmp_path.resolve(), "shell": False}
