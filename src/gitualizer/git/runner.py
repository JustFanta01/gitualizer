from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
from typing import Optional, Union


class GitError(RuntimeError):
    def __init__(self, command: list[str], cwd: Optional[Path], returncode: int, stdout: str, stderr: str):
        self.command = command
        self.cwd = cwd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        rendered = " ".join(command)
        location = f" in {cwd}" if cwd else ""
        super().__init__(f"`{rendered}` failed{location} with exit code {returncode}: {stderr.strip()}")


@dataclass(frozen=True)
class GitResult:
    args: list[str]
    cwd: Optional[Path]
    stdout: str
    stderr: str
    returncode: int


class GitRunner:
    """Small wrapper around the real Git executable.

    Commands are always represented as argument arrays. This class is read/write
    capable in principle, but V0 callers only use read-only Git commands.
    """

    def __init__(self, git_executable: str = "git") -> None:
        self.git_executable = git_executable

    def run(
        self,
        args: list[str],
        cwd: Optional[Union[Path, str]] = None,
        *,
        check: bool = True,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> GitResult:
        command = [self.git_executable, *args]
        cwd_path = Path(cwd).resolve() if cwd is not None else None
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd_path,
                env=process_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            if not stderr:
                stderr = f"Command timed out after {timeout} seconds."
            completed = subprocess.CompletedProcess(command, 124, stdout, stderr)
        result = GitResult(
            args=command,
            cwd=cwd_path,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
        if check and completed.returncode != 0:
            raise GitError(command, cwd_path, completed.returncode, completed.stdout, completed.stderr)
        return result
