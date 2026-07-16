from __future__ import annotations

from pathlib import Path
from typing import Optional

from gitualizer.git.runner import GitRunner, remote_auth_environment
from gitualizer.operations.command_plan import CommandPlan, ExecutionResult, StepResult


REMOTE_AUTH_COMMANDS = frozenset({"clone", "fetch", "ls-remote", "pull", "push", "submodule"})


class CommandExecutor:
    def __init__(self, runner: Optional[GitRunner] = None) -> None:
        self.runner = runner or GitRunner()

    @staticmethod
    def requires_terminal_auth(plan: CommandPlan) -> bool:
        return any(
            len(step.args) > 1
            and step.args[0] == "git"
            and step.args[1] in REMOTE_AUTH_COMMANDS
            for step in plan.steps
        )

    def execute(self, plan: CommandPlan, cwd: Path) -> ExecutionResult:
        results: list[StepResult] = []
        for step in plan.steps:
            args = step.args
            if not args or args[0] != "git":
                raise ValueError("Command plans must contain explicit git argument arrays.")
            command_name = args[1] if len(args) > 1 else ""
            if command_name in REMOTE_AUTH_COMMANDS:
                result = self.runner.run_interactive(
                    args[1:], cwd=cwd, env=remote_auth_environment(interactive=True)
                )
            else:
                result = self.runner.run(
                    args[1:],
                    cwd=cwd,
                    check=False,
                    env={
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_ASKPASS": "echo",
                        "SSH_ASKPASS": "echo",
                    },
                    timeout=120,
                )
            results.append(
                StepResult(
                    args=args,
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            )
            if result.returncode != 0:
                return ExecutionResult(success=False, steps=results)
        return ExecutionResult(success=True, steps=results)
