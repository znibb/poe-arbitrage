from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    """Run `fn(*args, **kwargs)` on the global thread pool; results return via `signals`."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # surfaced in the UI; never crash the worker thread silently
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.signals.done.emit(result)
