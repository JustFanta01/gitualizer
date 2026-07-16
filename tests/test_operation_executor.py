from pathlib import Path

from gitualizer.git.runner import GitResult
from gitualizer.operations.command_plan import CommandPlan, CommandStep
from gitualizer.operations.executor import CommandExecutor


class RecordingRunner:
    def __init__(self) -> None:
        self.interactive_calls = []
        self.noninteractive_calls = []

    def run_interactive(self, args, cwd=None, **kwargs):
        self.interactive_calls.append((args, cwd))
        return GitResult(["git", *args], cwd, "", "", 0)

    def run(self, args, cwd=None, **kwargs):
        self.noninteractive_calls.append((args, cwd, kwargs))
        return GitResult(["git", *args], cwd, "", "", 0)


def test_remote_commands_use_inherited_terminal(tmp_path: Path) -> None:
    runner = RecordingRunner()
    plan = CommandPlan(
        "Synchronize",
        "",
        [
            CommandStep(["git", "push", "origin", "main"], ""),
            CommandStep(["git", "fetch", "origin"], ""),
        ],
    )

    executor = CommandExecutor(runner)
    result = executor.execute(plan, tmp_path)

    assert result.success
    assert executor.requires_terminal_auth(plan)
    assert runner.interactive_calls == [
        (["push", "origin", "main"], tmp_path),
        (["fetch", "origin"], tmp_path),
    ]
    assert runner.noninteractive_calls == []


def test_other_commands_remain_noninteractive(tmp_path: Path) -> None:
    runner = RecordingRunner()
    plan = CommandPlan("Commit", "", [CommandStep(["git", "commit", "-m", "message"], "")])

    executor = CommandExecutor(runner)
    result = executor.execute(plan, tmp_path)

    assert result.success
    assert not executor.requires_terminal_auth(plan)
    assert runner.interactive_calls == []
    assert runner.noninteractive_calls[0][0] == ["commit", "-m", "message"]
