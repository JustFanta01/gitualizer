from __future__ import annotations

from pathlib import Path
from typing import Optional

from gitualizer.git.runner import GitRunner
from gitualizer.operations.command_plan import CommandPlan, ExecutionResult, StepResult


class CommandExecutor:
    def __init__(self, runner: Optional[GitRunner] = None) -> None:
        self.runner = runner or GitRunner()

    def execute(self, plan: CommandPlan, cwd: Path) -> ExecutionResult:
        results: list[StepResult] = []
        for step in plan.steps:
            args = step.args
            if not args or args[0] != "git":
                raise ValueError("Command plans must contain explicit git argument arrays.")
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
