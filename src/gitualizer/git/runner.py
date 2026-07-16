from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import subprocess
import tempfile
from typing import Callable, Optional, Union


AUTH_SESSION_SECONDS = 300


def remote_auth_environment(*, interactive: bool) -> dict[str, str]:
    """Configure Git/SSH-owned, short-lived authentication reuse."""
    user_id = os.getuid() if hasattr(os, "getuid") else os.getpid()
    control_path = Path(tempfile.gettempdir()) / f"gitualizer-ssh-{user_id}-%C"
    ssh_options = [
        "ssh",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPersist={AUTH_SESSION_SECONDS}",
        "-o", f"ControlPath={control_path}",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
    ]
    if not interactive:
        ssh_options.extend(["-o", "BatchMode=yes"])
    return {
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": f"cache --timeout={AUTH_SESSION_SECONDS}",
        "GIT_CONFIG_KEY_2": "http.lowSpeedLimit",
        "GIT_CONFIG_VALUE_2": "1",
        "GIT_CONFIG_KEY_3": "http.lowSpeedTime",
        "GIT_CONFIG_VALUE_3": "10",
        "GIT_SSH_COMMAND": " ".join(str(option) for option in ssh_options),
    }


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


@dataclass(frozen=True)
class CommandEvent:
    phase: str
    command: tuple[str, ...]
    cwd: Optional[Path]
    interactive: bool
    timestamp: str
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""


class GitRunner:
    """Small wrapper around the real Git executable.

    Commands are always represented as argument arrays. This class is read/write
    capable in principle, but V0 callers only use read-only Git commands.
    """

    def __init__(self, git_executable: str = "git") -> None:
        self.git_executable = git_executable
        self._observer: Optional[Callable[[CommandEvent], None]] = None

    def set_observer(self, observer: Optional[Callable[[CommandEvent], None]]) -> None:
        self._observer = observer

    def _emit(
        self,
        phase: str,
        command: list[str],
        cwd: Optional[Path],
        *,
        interactive: bool,
        result: Optional[GitResult] = None,
    ) -> None:
        if self._observer is None:
            return
        self._observer(
            CommandEvent(
                phase=phase,
                command=tuple(command),
                cwd=cwd,
                interactive=interactive,
                timestamp=datetime.now().astimezone().strftime("%H:%M:%S"),
                returncode=result.returncode if result else None,
                stdout=result.stdout if result else "",
                stderr=result.stderr if result else "",
            )
        )

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
        self._emit("started", command, cwd_path, interactive=False)
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
        except OSError as exc:
            failed = GitResult(command, cwd_path, "", str(exc), 126)
            self._emit("finished", command, cwd_path, interactive=False, result=failed)
            raise
        result = GitResult(
            args=command,
            cwd=cwd_path,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
        self._emit("finished", command, cwd_path, interactive=False, result=result)
        if check and completed.returncode != 0:
            raise GitError(command, cwd_path, completed.returncode, completed.stdout, completed.stderr)
        return result

    def run_interactive(
        self,
        args: list[str],
        cwd: Optional[Union[Path, str]] = None,
        *,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> GitResult:
        """Run Git with the parent's terminal and environment.

        Standard input and output are deliberately inherited. This lets Git,
        SSH, and configured credential helpers perform authentication without
        exposing credentials to Gitualizer.
        """
        command = [self.git_executable, *args]
        cwd_path = Path(cwd).resolve() if cwd is not None else None
        run_kwargs = {"cwd": cwd_path, "shell": False, "timeout": timeout}
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
            run_kwargs["env"] = process_env
        self._emit("started", command, cwd_path, interactive=True)
        try:
            completed = subprocess.run(command, **run_kwargs)
        except subprocess.TimeoutExpired:
            completed = subprocess.CompletedProcess(
                command,
                124,
                "",
                f"Command timed out after {timeout} seconds.",
            )
        except OSError as exc:
            failed = GitResult(command, cwd_path, "", str(exc), 126)
            self._emit("finished", command, cwd_path, interactive=True, result=failed)
            raise
        result = GitResult(
            command,
            cwd_path,
            completed.stdout if isinstance(completed.stdout, str) else "",
            completed.stderr if isinstance(completed.stderr, str) else "",
            completed.returncode,
        )
        self._emit("finished", command, cwd_path, interactive=True, result=result)
        return result
