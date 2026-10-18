"""Bridge SIGUSR1 into the Qt event loop.

Python-level signal handlers only run when the interpreter gets control, which a blocked Qt
loop does not give it. `signal.set_wakeup_fd` makes the C-level handler write to a socket,
which a QSocketNotifier turns into a normal Qt signal.
"""

import signal
import socket

from PySide6.QtCore import QObject, QSocketNotifier, Signal


class Sigusr1Bridge(QObject):
    triggered = Signal()  # SIGUSR1: start a scan
    quit_requested = Signal()  # SIGINT / SIGTERM: shut down cleanly (removes the pid file)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._r, self._w = socket.socketpair()
        self._r.setblocking(False)
        self._w.setblocking(False)
        for sig in (signal.SIGUSR1, signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: None)  # replace default actions; the wakeup fd does the work
        signal.set_wakeup_fd(self._w.fileno(), warn_on_full_buffer=False)
        self._notifier = QSocketNotifier(self._r.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = self._r.recv(64)
        except BlockingIOError:
            return
        if signal.SIGUSR1 in data:
            self.triggered.emit()
        if signal.SIGINT in data or signal.SIGTERM in data:
            self.quit_requested.emit()

    def close(self) -> None:
        signal.set_wakeup_fd(-1)
        self._r.close()
        self._w.close()
