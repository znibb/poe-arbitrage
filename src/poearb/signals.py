"""Process control without Qt: pid file, single-instance check, and the SIGUSR1 trigger.

The running GUI writes its pid to `$XDG_RUNTIME_DIR/poearb.pid`; `poearb trigger` (what the
OS-level global shortcut runs) reads it and sends SIGUSR1 to start a scan.
"""

import os
import signal
from pathlib import Path


def pid_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "poearb.pid"


def _is_poearb(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"poearb" in cmdline


def running_pid(path: Path | None = None) -> int | None:
    """Pid of a live poearb process recorded in the pid file, else None."""
    path = path or pid_path()
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _is_poearb(pid) else None


def write_pid(path: Path | None = None) -> None:
    (path or pid_path()).write_text(str(os.getpid()))


def remove_pid(path: Path | None = None) -> None:
    path = path or pid_path()
    try:
        if path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except (OSError, ValueError):
        pass


def send_trigger(path: Path | None = None) -> bool:
    """Send SIGUSR1 to the running poearb. False if none is running."""
    pid = running_pid(path)
    if pid is None:
        return False
    os.kill(pid, signal.SIGUSR1)
    return True
