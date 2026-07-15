from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandStep:
    args: list[str]
    explanation: str

    @property
    def display(self) -> str:
        return " ".join(_quote_arg(arg) for arg in self.args)


@dataclass(frozen=True)
class CommandPlan:
    title: str
    explanation: str
    steps: list[CommandStep]
    expected_effects: list[str] = field(default_factory=list)
    preview_steps: list[str] = field(default_factory=list)
    graph_preview: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    history_rewrite: bool = False
    destructive: bool = False
    remote_impact: str = "None"
    state_fingerprint: str = ""

    @property
    def commands_text(self) -> str:
        return "\n".join(step.display for step in self.steps)


@dataclass(frozen=True)
class StepResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    steps: list[StepResult]


def _quote_arg(arg: str) -> str:
    if not arg:
        return "''"
    if all(char.isalnum() or char in "-_./:@=" for char in arg):
        return arg
    return "'" + arg.replace("'", "'\"'\"'") + "'"
