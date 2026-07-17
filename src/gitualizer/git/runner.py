from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import signal
import subprocess
import tempfile
import threading
from typing import Callable, Optional, Union


AUTH_SESSION_SECONDS = 300
INTERACTIVE_COMMAND_TIMEOUT = 300
INTERRUPT_SIGNALS = (signal.SIGINT,)
_active_interactive_commands = 0
_terminal_interrupt_callback: Optional[Callable[[], None]] = None


def install_terminal_interrupt_protection(on_interrupt: Optional[Callable[[], None]] = None) -> None:
    """Let Ctrl+C cancel interactive Git without closing Gitualizer."""
    if threading.current_thread() is not threading.main_thread():
        return
    global _terminal_interrupt_callback
    _terminal_interrupt_callback = on_interrupt
    signal.signal(signal.SIGINT, _handle_terminal_interrupt)


def _handle_terminal_interrupt(signum: int, frame: object) -> None:
    if _active_interactive_commands:
        return
    if _terminal_interrupt_callback is not None:
        _terminal_interrupt_callback()
        return
    raise KeyboardInterrupt


def _restore_interactive_child_signals() -> None:
    for sig in INTERRUPT_SIGNALS:
        signal.signal(sig, signal.SIG_DFL)


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
                # History events are an execution audit trail, not a second
                # command-output buffer. Results remain with their callers.
                stdout="",
                stderr="",
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
        timeout: Optional[float] = INTERACTIVE_COMMAND_TIMEOUT,
    ) -> GitResult:
        """Run Git with the parent's terminal and environment.

        Standard input and output are deliberately inherited. This lets Git,
        SSH, and configured credential helpers perform authentication without
        exposing credentials to Gitualizer.
        """
        command = [self.git_executable, *args]
        cwd_path = Path(cwd).resolve() if cwd is not None else None
        run_kwargs = {"cwd": cwd_path, "shell": False}
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
            run_kwargs["env"] = process_env
        if os.name == "posix":
            run_kwargs["preexec_fn"] = _restore_interactive_child_signals
        if timeout is not None:
            run_kwargs["timeout"] = timeout
        self._emit("started", command, cwd_path, interactive=True)
        global _active_interactive_commands
        _active_interactive_commands += 1
        try:
            completed = subprocess.run(command, **run_kwargs)
        except subprocess.TimeoutExpired:
            completed = subprocess.CompletedProcess(
                command,
                124,
                stderr=f"Command timed out after {timeout} seconds.",
            )
        except KeyboardInterrupt:
            completed = subprocess.CompletedProcess(command, 130, stderr="Command interrupted.")
        except OSError as exc:
            failed = GitResult(command, cwd_path, "", str(exc), 126)
            self._emit("finished", command, cwd_path, interactive=True, result=failed)
            raise
        finally:
            _active_interactive_commands -= 1
        result = GitResult(command, cwd_path, "", completed.stderr or "", completed.returncode)
        self._emit("finished", command, cwd_path, interactive=True, result=result)
        return result
