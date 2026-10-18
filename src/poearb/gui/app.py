import sys

from PySide6.QtWidgets import QApplication

from .. import ocr, signals
from .main_window import MainWindow
from .sigbridge import Sigusr1Bridge


def run(cfg: dict) -> int:
    existing = signals.running_pid()
    if existing:
        print(f"poearb is already running (pid {existing})", file=sys.stderr)
        return 1
    ocr.configure(cfg.get("ocr", {}).get("tessdata_dir"))

    app = QApplication(sys.argv[:1])
    bridge = Sigusr1Bridge()
    win = MainWindow(cfg, bridge)
    bridge.quit_requested.connect(app.quit)
    signals.write_pid()
    try:
        win.show()
        return app.exec()
    finally:
        bridge.close()
        signals.remove_pid()
