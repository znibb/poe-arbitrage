"""Spectacle-style region picker.

Wayland apps cannot draw over other windows, so this shows a frozen screenshot of one
monitor fullscreen and lets the user move/resize a rectangle on it.
Enter accepts, Esc cancels, Tab moves to the next monitor.
"""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QWidget

HANDLE = 10
MIN_SIZE = 20
DEFAULT_SIZE = (600, 300)


class RegionSelector(QWidget):
    accepted = Signal(dict)  # region in screenshot pixels: {x, y, w, h}
    cancelled = Signal()
    next_screen = Signal()

    def __init__(self, shot: QImage, screen: QScreen, initial: dict | None = None) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setScreen(screen)

        geom = screen.geometry()
        virt = QGuiApplication.primaryScreen().virtualGeometry()
        dpr = screen.devicePixelRatio()
        # This monitor's slice of the all-monitor screenshot, in screenshot pixels.
        self._origin = QPoint(round((geom.x() - virt.x()) * dpr), round((geom.y() - virt.y()) * dpr))
        self._slice = shot.copy(QRect(self._origin, geom.size() * dpr))
        self._k = self._slice.width() / geom.width()  # screenshot pixels per widget unit
        self.setGeometry(geom)

        self._sel = self._initial_rect(initial, geom.width(), geom.height())
        self._drag: tuple[str, QPointF, QRectF] | None = None

    def _initial_rect(self, region: dict | None, w: int, h: int) -> QRectF:
        if region:
            r = QRectF(
                (region["x"] - self._origin.x()) / self._k,
                (region["y"] - self._origin.y()) / self._k,
                region["w"] / self._k,
                region["h"] / self._k,
            )
            if r.intersects(QRectF(0, 0, w, h)):
                return r
        dw, dh = DEFAULT_SIZE
        return QRectF((w - dw) / 2, (h - dh) / 2, dw, dh)

    def region(self) -> dict:
        r, k = self._sel.normalized(), self._k
        return {
            "x": round(r.x() * k) + self._origin.x(),
            "y": round(r.y() * k) + self._origin.y(),
            "w": round(r.width() * k),
            "h": round(r.height() * k),
        }

    # -- painting -----------------------------------------------------------------
    def _handles(self) -> dict[str, QRectF]:
        r = self._sel
        cx, cy = r.center().x(), r.center().y()
        pts = {
            "tl": (r.left(), r.top()), "t": (cx, r.top()), "tr": (r.right(), r.top()),
            "r": (r.right(), cy), "br": (r.right(), r.bottom()), "b": (cx, r.bottom()),
            "bl": (r.left(), r.bottom()), "l": (r.left(), cy),
        }
        return {n: QRectF(x - HANDLE / 2, y - HANDLE / 2, HANDLE, HANDLE) for n, (x, y) in pts.items()}

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.drawImage(self.rect(), self._slice)
        dim = QPainterPath()
        dim.addRect(QRectF(self.rect()))
        dim.addRect(self._sel)
        p.fillPath(dim, QColor(0, 0, 0, 140))  # odd-even fill leaves the selection undimmed
        p.setPen(QPen(QColor("#3daee9"), 2))
        p.drawRect(self._sel)
        p.setBrush(QColor("#3daee9"))
        for h in self._handles().values():
            p.drawRect(h)
        r = self.region()
        label = f"{r['w']} x {r['h']}  at ({r['x']}, {r['y']})   Enter: capture   Esc: cancel   Tab: next monitor"
        p.setPen(Qt.GlobalColor.white)
        p.setBrush(QColor(0, 0, 0, 180))
        text_rect = p.fontMetrics().boundingRect(label).adjusted(-8, -4, 8, 4)
        text_rect.moveTopLeft(QPoint(int(self._sel.left()), max(0, int(self._sel.top()) - text_rect.height() - 6)))
        p.drawRect(text_rect)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

    # -- interaction --------------------------------------------------------------
    def _hit(self, pos: QPointF) -> str | None:
        for name, h in self._handles().items():
            if h.adjusted(-4, -4, 4, 4).contains(pos):
                return name
        return "move" if self._sel.contains(pos) else None

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        hit = self._hit(pos)
        if hit is None:  # start a fresh selection
            self._sel = QRectF(pos, pos)
            hit = "br"
        self._drag = (hit, pos, QRectF(self._sel))
        self.update()

    def mouseMoveEvent(self, e) -> None:
        if not self._drag:
            return
        mode, start, orig = self._drag
        d = e.position() - start
        r = QRectF(orig)
        if mode == "move":
            r.translate(d)
        else:
            if "l" in mode:
                r.setLeft(orig.left() + d.x())
            if "r" in mode:
                r.setRight(orig.right() + d.x())
            if "t" in mode:
                r.setTop(orig.top() + d.y())
            if "b" in mode:
                r.setBottom(orig.bottom() + d.y())
        self._sel = r
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag:
            self._sel = self._sel.normalized()
            self._drag = None
            self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            r = self.region()
            if r["w"] >= MIN_SIZE and r["h"] >= MIN_SIZE:
                self.accepted.emit(r)
                self.close()
        elif e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        elif e.key() == Qt.Key.Key_Tab:
            self.next_screen.emit()
            self.close()
