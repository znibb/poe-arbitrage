import time
from datetime import datetime

from PIL import Image
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import analyze, capture, ninja, scan
from ..config import load_gold_cost, save_region, save_setting
from ..models import ScanResult, TradeRow
from .region_selector import RegionSelector
from .sigbridge import Sigusr1Bridge
from .workers import Worker

MAX_N = 500
MIRROR = "Mirror of Kalandra"
LOCK = "Hinekora's Lock"
EXALTED = "Exalted Orb"
MUTED = "color: palette(placeholder-text); font-size: 9pt;"
BUSY = "color: #f67400; font-size: 10pt; font-weight: bold;"  # background work in progress


def parse_top_n(text: str) -> int | None:
    """A whole number between 1 and MAX_N, else None. ASCII digits only (no '1_0', '٣')."""
    text = text.strip()
    if text.isascii() and text.isdigit() and 1 <= int(text) <= MAX_N:
        return int(text)
    return None


def fetch_leagues(cfg: dict) -> dict:
    """Worker: the league list (data/ninja/leagues.json; re-fetched from poe.ninja only if stale)."""
    got = ninja.get_leagues(**ninja.kwargs_from_config(cfg))
    return {
        "leagues": [(lg.get("name") or lg["id"], lg["id"]) for lg in got.data],
        "fetched_at": got.fetched_at,
        "from_cache": got.from_cache,
        "stale": got.stale,
    }


def fetch_volume(cfg: dict, n: int, ignore: tuple[str, ...] = (), league: str = "") -> dict:
    """Worker: refresh `league`'s poe.ninja data if stale and rank the top `n` items by volume,
    leaving out the `ignore`d items and the core orbs (they do not count towards `n`; the core
    pairs are still in the scan plan, as their relative prices are what profits are computed from)."""
    ignore = tuple(dict.fromkeys((*ignore, *analyze.CORE_ORBS)))
    kw = ninja.kwargs_from_config(cfg)
    overviews = ninja.get_all_overviews(league, **kw)
    rates = overviews["Currency"].data["core"].get("rates", {}) if "Currency" in overviews else {}
    core_prices = {"Divine Orb": 1.0}  # divine per unit, from poe.ninja's per-divine rates
    for name, key in (("Chaos Orb", "chaos"), ("Exalted Orb", "exalted")):
        if rates.get(key):
            core_prices[name] = 1 / rates[key]
    return {
        "core_prices": core_prices,
        "league": league,
        "rows": [r for r in ninja.top_by_volume(overviews, n + len(ignore)) if r["name"] not in ignore][:n],
        "fetched_at": min(o.fetched_at for o in overviews.values()),
        "from_cache": all(o.from_cache for o in overviews.values()),
        "stale": any(o.stale for o in overviews.values()),
    }


def _qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    return QImage(rgb.tobytes(), rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888).copy()


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fit_rows(table: QTableWidget, rows: int) -> None:
    """Make the table just tall enough for `rows` rows (no scroll bar)."""
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    height = table.horizontalHeader().sizeHint().height() + rows * table.verticalHeader().defaultSectionSize()
    table.setFixedHeight(height + 2 * table.frameWidth() + 2)


def _item(text: str, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def _short(orb: str) -> str:
    return orb.removesuffix(" Orb")


def _fmt_ratio(r: tuple[float, float] | None) -> str:
    return "?" if r is None else f"{r[0]:g} : {r[1]:g}"


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, bridge: Sigusr1Bridge | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("poearb")
        self.resize(1500, 800)
        self._pool = QThreadPool.globalInstance()
        self._workers: list[Worker] = []
        self._scanning = False
        self._checking_volume = False
        self._shot: Image.Image | None = None
        self._selector: RegionSelector | None = None
        self._screen_idx = 0

        # -- controls
        self.n_edit = QLineEdit(str(cfg.get("ninja", {}).get("top_n", 20)))
        self.n_edit.setFixedWidth(70)
        self.n_edit.setToolTip(f"Whole number, 1-{MAX_N}")
        self.leagues_btn = QPushButton("Fetch leagues")
        self.league_combo = QComboBox()
        self.league_combo.setMinimumWidth(180)
        self.league_combo.setPlaceholderText("Select league")
        self.league_combo.setEnabled(False)
        self.volume_btn = QPushButton("Check trade volume")
        self.volume_btn.setEnabled(False)  # until a league is selected
        self.volume_btn.setToolTip("Select a league first (Fetch leagues lists them)")
        saved_league = cfg.get("ninja", {}).get("league")
        if saved_league:  # the last used league is in place right away, no fetch needed
            self.league_combo.addItem(saved_league, saved_league)
            self.league_combo.setCurrentIndex(0)
            self.league_combo.setEnabled(True)
            self.volume_btn.setEnabled(True)
        self.calibrate_btn = QPushButton("Calibrate capture")
        self.ignore_mirror = QCheckBox(f"Ignore {MIRROR}")
        gui_cfg = cfg.get("gui", {})
        self.ignore_mirror.setChecked(bool(gui_cfg.get("ignore_mirror", True)))
        self.ignore_mirror.setToolTip("Too expensive to be a viable trade step; left out of the table and plan")
        self.ignore_lock = QCheckBox(f"Ignore {LOCK}")
        self.ignore_lock.setChecked(bool(gui_cfg.get("ignore_lock", True)))
        self.ignore_lock.setToolTip("Too expensive to be a viable trade step; left out of the table and plan")
        self.ignore_exalted = QCheckBox(f"Ignore {EXALTED}")
        self.ignore_exalted.setChecked(bool(gui_cfg.get("ignore_exalted", False)))
        self.ignore_exalted.setToolTip(
            "Leave the Exalted Orb out of the core currencies (late in a league it is worth very little):"
            " only Chaos and Divine form the round trips"
        )
        self.ignore_gold = QCheckBox("Ignore gold cost")
        self.ignore_gold.setChecked(bool(gui_cfg.get("ignore_gold_cost", True)))
        self.ignore_gold.setToolTip(
            "Checked: rank round trips by trade profit only. Unchecked: by profit per 1000 gold."
        )
        top = QHBoxLayout()  # spans the whole window, above the columns
        top.addWidget(self.leagues_btn)
        top.addWidget(self.league_combo)
        top.addSpacing(16)
        top.addWidget(QLabel("Top N:"))
        top.addWidget(self.n_edit)
        top.addWidget(self.volume_btn)
        top.addWidget(self.ignore_mirror)
        top.addWidget(self.ignore_lock)
        top.addWidget(self.ignore_exalted)
        top.addWidget(self.ignore_gold)
        top.addStretch()
        top.addWidget(self.calibrate_btn)

        self.n_error = QLabel()
        self.n_error.setStyleSheet("color: #da4453;")
        self.n_error.hide()

        # -- volume table
        self.volume_table = _table(["#", "Item", "Category", "Volume", "Price", "Busiest pair"])

        # -- scan panel
        self.preview = QLabel("No scan yet")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.preview.setFixedWidth(360)
        self.scan_info = QLabel()
        self.scan_info.setTextFormat(Qt.TextFormat.RichText)
        self.scan_info.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scan_info.setWordWrap(True)
        self.avail_table = _table(["Ratio", "Stock"])
        self.comp_table = _table(["Ratio", "Stock"])
        for table in (self.avail_table, self.comp_table):
            _fit_rows(table, 6)  # the game never shows more than 6 rows per table
        tables = QHBoxLayout()
        for title, table in (("Available trades", self.avail_table), ("Competing trades", self.comp_table)):
            col = QVBoxLayout()
            col.addWidget(QLabel(title))
            col.addWidget(table)
            tables.addLayout(col)
        right = QVBoxLayout()
        right.addWidget(self.scan_info)
        right.addLayout(tables)
        scan_row = QHBoxLayout()
        scan_row.addWidget(self.preview)
        scan_row.addLayout(right, 1)
        scan_box = QGroupBox("Last scan (SIGUSR1)")
        scan_box.setLayout(scan_row)

        # -- triangle plan (core orbs round trips)
        self._plan: list[analyze.ScanStep] = []
        self._plan_done: dict[frozenset, str] = {}  # keyed by the pair, either orientation
        self.plan_info = QLabel("Run 'Check trade volume' to build the scan order.")
        self.plan_info.setWordWrap(True)
        self.plan_info.setMinimumHeight(48)
        self.plan_table = _table(["#", "Scan: want", "with (have)", "Purpose", "Status"])
        plan_col = QVBoxLayout()
        plan_col.addWidget(self.plan_info)
        plan_col.addWidget(self.plan_table)
        self.plan_box = plan_box = QGroupBox()
        self._update_plan_title()
        plan_box.setLayout(plan_col)

        # -- round-trip profit
        self._books: dict[frozenset, analyze.Book] = {}
        self._last_rows: list[dict] | None = None
        self._core_prices: dict[str, float] = {}
        try:
            self._gold_cost = load_gold_cost()
        except (OSError, ValueError):
            self._gold_cost = {}
        self.profit_info = QLabel("Scan the pairs in the order above to see profits.")
        self.profit_info.setWordWrap(True)
        self.profit_info.setMinimumHeight(20)
        self.profit_table = _table(
            ["#", "Round trip", "Start", "Profit", "Div", "Margin", "Gold", "/1000 g", "Age", "Notes"]
        )
        profit_col = QVBoxLayout()
        profit_col.addWidget(self.profit_info)
        profit_col.addWidget(self.profit_table)
        profit_box = QGroupBox("Round-trip profit (A -> item -> B -> A)")
        profit_box.setLayout(profit_col)
        right_col = QVBoxLayout()
        right_col.addWidget(plan_box, 1)
        right_col.addWidget(profit_box, 1)

        # -- status bar: discreet feedback
        self.data_status = QLabel("poe.ninja data: not loaded")
        self.scan_status = QLabel("")
        for lbl in (self.data_status, self.scan_status):
            lbl.setStyleSheet(MUTED)
        status = QHBoxLayout()
        status.addWidget(self.data_status)
        status.addStretch()
        status.addWidget(self.scan_status)

        left = QVBoxLayout()
        left.addWidget(self.volume_table, 1)
        left.addWidget(scan_box)
        columns = QHBoxLayout()
        columns.addLayout(left, 3)
        columns.addLayout(right_col, 3)  # scan order and profits get their own column on the right
        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.n_error)
        layout.addLayout(columns, 1)
        layout.addLayout(status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.leagues_btn.clicked.connect(self.fetch_leagues)
        self.league_combo.currentIndexChanged.connect(self._league_changed)
        self.volume_btn.clicked.connect(self.check_volume)
        self.n_edit.returnPressed.connect(self.check_volume)
        self.calibrate_btn.clicked.connect(self.calibrate)
        self.ignore_mirror.toggled.connect(self._ignore_toggled)
        self.ignore_lock.toggled.connect(self._ignore_toggled)
        self.ignore_exalted.toggled.connect(self._exalted_toggled)
        self.ignore_gold.toggled.connect(self._gold_toggled)
        if bridge:
            bridge.triggered.connect(self.start_scan)

    # -- helpers --------------------------------------------------------------------
    def _run(self, fn, on_done, on_fail, *args) -> None:
        w = Worker(fn, *args)
        self._workers.append(w)

        def cleanup() -> None:
            if w in self._workers:
                self._workers.remove(w)

        w.signals.done.connect(on_done)
        w.signals.failed.connect(on_fail)
        w.signals.done.connect(cleanup)
        w.signals.failed.connect(cleanup)
        self._pool.start(w)

    def _set_scan_status(self, text: str, busy: bool = False) -> None:
        self.scan_status.setStyleSheet(BUSY if busy else MUTED)
        self.scan_status.setText(text)

    # -- trade volume ---------------------------------------------------------------
    def selected_league(self) -> str | None:
        return self.league_combo.currentData() if self.league_combo.currentIndex() >= 0 else None

    def _update_volume_enabled(self) -> None:
        self.volume_btn.setEnabled(self.selected_league() is not None and not self._checking_volume)

    def fetch_leagues(self) -> None:
        self.leagues_btn.setEnabled(False)
        self.data_status.setText("poe.ninja leagues: loading...")
        self._run(fetch_leagues, self._leagues_done, self._leagues_failed, self.cfg)

    def _leagues_done(self, res: dict) -> None:
        self.leagues_btn.setEnabled(True)
        remembered = self.cfg.get("ninja", {}).get("league")
        combo = self.league_combo
        combo.blockSignals(True)
        combo.clear()
        for name, league_id in res["leagues"]:
            combo.addItem(name, league_id)
        combo.setEnabled(bool(res["leagues"]))
        # The previously chosen league is selected again; otherwise the user has to pick one.
        combo.setCurrentIndex(max(-1, combo.findData(remembered)) if remembered else -1)
        combo.blockSignals(False)
        self._update_volume_enabled()
        stamp = datetime.fromtimestamp(res["fetched_at"]).strftime("%Y-%m-%d %H:%M")
        src = "cached" if res["from_cache"] else "fetched"
        stale = " (stale: refresh failed)" if res["stale"] else ""
        self.data_status.setText(f"poe.ninja leagues: {len(res['leagues'])}, {stamp} ({src}){stale}")

    def _leagues_failed(self, msg: str) -> None:
        self.leagues_btn.setEnabled(True)
        self.data_status.setText(f"poe.ninja leagues: failed ({msg})")

    def _league_changed(self) -> None:
        """A different league is a different economy: drop the scans and plan of the old one."""
        league = self.selected_league()
        self._update_volume_enabled()
        if league is None:
            return
        self.cfg.setdefault("ninja", {})["league"] = league
        try:
            save_setting("ninja", "league", league)
        except OSError as e:
            self._set_scan_status(f"Could not save league: {e}")
        if self._books or self._plan:
            self._books.clear()
            self._plan, self._plan_done, self._last_rows = [], {}, None
            self.volume_table.setRowCount(0)
            self._refresh_plan()
            self._refresh_profit()
            self.plan_info.setText("League changed: run 'Check trade volume' to build the scan order.")

    def check_volume(self) -> None:
        league = self.selected_league()
        if league is None:  # the button is disabled in this state; Enter in the field gets here
            return
        n = parse_top_n(self.n_edit.text())
        if n is None:
            self.n_error.setText(f"Enter a whole number between 1 and {MAX_N}.")
            self.n_error.show()
            return
        self.n_error.hide()
        self._remember_top_n(n)
        self._checking_volume = True
        self._update_volume_enabled()
        self.data_status.setText("poe.ninja data: checking...")
        ignore = tuple(
            name for name, box in ((MIRROR, self.ignore_mirror), (LOCK, self.ignore_lock)) if box.isChecked()
        )
        self._run(fetch_volume, self._volume_done, self._volume_failed, self.cfg, n, ignore, league)

    def _remember_top_n(self, n: int) -> None:
        """Keep the last used N (in config.local.yaml) for the next start."""
        self.cfg.setdefault("ninja", {})["top_n"] = n
        try:
            save_setting("ninja", "top_n", n)
        except OSError as e:
            self._set_scan_status(f"Could not save Top N: {e}")

    def _save_checkboxes(self) -> None:
        state = {
            "ignore_mirror": self.ignore_mirror.isChecked(),
            "ignore_lock": self.ignore_lock.isChecked(),
            "ignore_exalted": self.ignore_exalted.isChecked(),
            "ignore_gold_cost": self.ignore_gold.isChecked(),
        }
        self.cfg.setdefault("gui", {}).update(state)
        try:
            for key, val in state.items():
                save_setting("gui", key, val)
        except OSError as e:
            self._set_scan_status(f"Could not save settings: {e}")

    def _gold_toggled(self) -> None:
        self._save_checkboxes()
        self._refresh_profit()

    def cores(self) -> tuple[str, ...]:
        """The core currencies the round trips run through."""
        return tuple(c for c in analyze.CORE_ORBS if not (c == EXALTED and self.ignore_exalted.isChecked()))

    def _update_plan_title(self) -> None:
        names = " / ".join(c.removesuffix(" Orb") for c in self.cores())
        self.plan_box.setTitle(f"Triangle scan order ({names})")

    def _exalted_toggled(self) -> None:
        """No network needed: the plan is rebuilt from the last volume result. Scans already
        taken stay valid (their pairs are still in the plan, or simply unused)."""
        self._save_checkboxes()
        self._update_plan_title()
        if self._last_rows is not None:
            self._build_plan(self._last_rows, self._core_prices, keep_done=True)
        self._refresh_profit()

    def _ignore_toggled(self) -> None:
        self._save_checkboxes()
        if self.volume_table.rowCount():  # already showing results: redo them (cheap, uses the cache)
            self.check_volume()

    def _volume_done(self, res: dict) -> None:
        self._checking_volume = False
        self._update_volume_enabled()
        t = self.volume_table
        t.setRowCount(len(res["rows"]))
        for i, r in enumerate(res["rows"]):
            vals = [str(i + 1), r["name"], r["category"], f"{r['volume']:,.0f}", f"{r['price']:.4g}",
                    r["max_volume_currency"] or ""]
            for c, v in enumerate(vals):
                t.setItem(i, c, _item(v, right=c in (0, 3, 4)))
        self._core_prices = res.get("core_prices") or {}
        self._last_rows = res["rows"]
        self._build_plan(res["rows"], res.get("core_prices"))
        self._refresh_profit()
        stamp = datetime.fromtimestamp(res["fetched_at"]).strftime("%Y-%m-%d %H:%M")
        src = "cached" if res["from_cache"] else "fetched"
        stale = " (stale: refresh failed)" if res["stale"] else ""
        self.data_status.setText(f"poe.ninja data: {stamp} ({src}), {res['league']}{stale}")

    # -- triangle plan --------------------------------------------------------------
    def _build_plan(
        self, rows: list[dict], core_prices: dict[str, float] | None = None, keep_done: bool = False
    ) -> None:
        self._plan = analyze.plan_scans(rows, cores=self.cores(), core_prices=core_prices)
        if not keep_done:
            self._plan_done = {}
        self._refresh_plan()

    def _refresh_plan(self) -> None:
        t = self.plan_table
        t.setRowCount(len(self._plan))
        nxt = next((i for i, st in enumerate(self._plan) if frozenset(st.pair) not in self._plan_done), None)
        for i, st in enumerate(self._plan):
            status = self._plan_done.get(frozenset(st.pair), "<- next" if i == nxt else "")
            for c, v in enumerate([str(i + 1), st.want, st.have, st.purpose, status]):
                it = _item(v, right=c == 0)
                if frozenset(st.pair) in self._plan_done:
                    it.setForeground(self.palette().placeholderText())
                t.setItem(i, c, it)
        if nxt is not None:
            t.scrollToItem(t.item(nxt, 0))
            st = self._plan[nxt]
            self.plan_info.setText(
                f"{len(self._plan_done)}/{len(self._plan)} scans done. Next: open the exchange with "
                f"I WANT {st.want}, I HAVE {st.have}, show the trades panes, then press the scan shortcut. "
                "One scan per pair covers both directions (available = asks, competing = bids)."
            )
        elif self._plan:
            self.plan_info.setText("All scans done.")

    # -- round-trip profit ----------------------------------------------------------
    def _plan_items(self) -> list[str]:
        return list(dict.fromkeys(n for st in self._plan for n in st.pair if n not in analyze.CORE_ORBS))

    def _refresh_profit(self) -> None:
        results, evaluable, total = analyze.rank_trips(
            self._plan_items(), self._books, self._gold_cost, self._core_prices,
            ignore_gold=self.ignore_gold.isChecked(), cores=self.cores(),
        )
        by = "profit" if self.ignore_gold.isChecked() else "profit per 1000 gold"
        self.profit_info.setText(
            f"{evaluable} of {total} round trips have all three pairs scanned; ranked by {by}."
        )
        t = self.profit_table
        t.setRowCount(len(results))
        now = time.time()
        for i, r in enumerate(results):
            a, x, b = r.route
            vals = [
                str(i + 1), f"{_short(a)} > {x} > {_short(b)} > {_short(a)}", f"{r.start:,.0f} {_short(a)}",
                f"{r.profit:+,.0f} {_short(a)}", f"{r.profit_div:+.3f}", f"{r.margin_pct:+.1f}%",
                f"{r.gold:,}", "-" if r.profit_per_1000_gold is None else f"{r.profit_per_1000_gold:.4f}",
                f"{(now - r.oldest_ts) / 60:.0f} min", "; ".join(r.notes),
            ]
            for c, v in enumerate(vals):
                it = _item(v, right=c in (0, 2, 3, 4, 5, 6, 7, 8))
                if r.profit <= 0:
                    it.setForeground(self.palette().placeholderText())
                t.setItem(i, c, it)

    def _mark_plan_done(self, res: ScanResult) -> None:
        book = analyze.book_from_scan(res)
        if book:
            self._books[frozenset((book.want, book.have))] = book
            self._refresh_profit()
        pair = frozenset((res.want_cur, res.have_cur))
        if (res.available or res.competing) and any(frozenset(st.pair) == pair for st in self._plan):
            seen = _fmt_ratio(res.market_ratio) if res.available else "no offers"
            self._plan_done[pair] = f"{time.strftime('%H:%M')}  {seen}"
            self._refresh_plan()

    def _volume_failed(self, msg: str) -> None:
        self._checking_volume = False
        self._update_volume_enabled()
        self.data_status.setText(f"poe.ninja data: failed ({msg})")

    # -- calibration ----------------------------------------------------------------
    def calibrate(self) -> None:
        self._screen_idx = max(0, QGuiApplication.screens().index(self.screen()))
        self.calibrate_btn.setEnabled(False)
        self.hide()  # keep this window out of the screenshot
        QTimer.singleShot(400, self._grab_for_calibration)

    def _grab_for_calibration(self) -> None:
        self._run(capture.grab_fullscreen, self._calibration_shot, self._calibration_failed)

    def _calibration_failed(self, msg: str) -> None:
        self.show()
        self.calibrate_btn.setEnabled(True)
        self._set_scan_status(f"Calibration failed: {msg}")

    def _calibration_shot(self, img: Image.Image) -> None:
        self._shot = img
        self._open_selector()

    def _open_selector(self) -> None:
        screens = QGuiApplication.screens()
        sel = RegionSelector(
            _qimage(self._shot), screens[self._screen_idx % len(screens)], self.cfg.get("capture", {}).get("region")
        )
        sel.accepted.connect(self._region_accepted)
        sel.cancelled.connect(self._calibration_finished)
        sel.next_screen.connect(self._next_screen)
        self._selector = sel
        sel.showFullScreen()
        sel.activateWindow()
        sel.setFocus()

    def _next_screen(self) -> None:
        self._screen_idx += 1
        QTimer.singleShot(0, self._open_selector)

    def _region_accepted(self, region: dict) -> None:
        save_region(region)
        self.cfg.setdefault("capture", {})["region"] = region
        self._set_scan_status(f"Capture region saved: {region['w']}x{region['h']} at ({region['x']}, {region['y']})")
        self._calibration_finished(keep_status=True)

    def _calibration_finished(self, keep_status: bool = False) -> None:
        self.show()
        self.activateWindow()
        self.calibrate_btn.setEnabled(True)
        if not keep_status:
            self._set_scan_status("Calibration cancelled")

    # -- scanning (SIGUSR1) ---------------------------------------------------------
    def start_scan(self) -> None:
        if self._scanning:
            self._set_scan_status("Scan already running; trigger ignored")
            return
        if not self.cfg.get("capture", {}).get("region"):
            self._set_scan_status("No capture region set: click 'Calibrate capture' first")
            return
        self._scanning = True
        self._set_scan_status("Scanning...", busy=True)
        self._run(scan.capture_and_scan, self._scan_done, self._scan_failed, self.cfg)

    def _scan_failed(self, msg: str) -> None:
        self._scanning = False
        self._set_scan_status(f"Scan failed: {msg}")

    def _scan_done(self, result: tuple[ScanResult, str | None]) -> None:
        self._scanning = False
        res, path = result
        if path:
            pix = QPixmap(path)
            self.preview.setPixmap(pix.scaledToWidth(self.preview.width(), Qt.TransformationMode.SmoothTransformation))
        lines = [
            f"<b>Want:</b> {res.want_cur or '?'} &nbsp; <b>Have:</b> {res.have_cur or '?'}",
            f"<b>Market ratio:</b> {_fmt_ratio(res.market_ratio)}",
            f"<b>Gold per {res.want_cur or 'item'}:</b> "
            f"{res.gold_per_item if res.gold_per_item is not None else '?'}",
        ]
        if res.no_offers:
            lines.append("<i>No available trades: nobody is offering this.</i>")
        if res.warnings:
            lines.append("<span style='color:#f67400'>" + "<br>".join(res.warnings) + "</span>")
        self.scan_info.setText("<br>".join(lines))
        self._fill_trades(self.avail_table, res.available)
        self._fill_trades(self.comp_table, res.competing)
        self._mark_plan_done(res)
        stored = "stored" if not res.warnings and res.market_ratio else "not stored"
        self._set_scan_status(f"Last scan: {time.strftime('%H:%M:%S')} ({stored}, OCR confidence {res.confidence:.0f}%)")

    @staticmethod
    def _fill_trades(table: QTableWidget, rows: list[TradeRow]) -> None:
        table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            marker = f"{r.aggregate} " if r.aggregate else ""
            table.setItem(i, 0, _item(f"{marker}{_fmt_ratio(r.ratio)}"))
            table.setItem(i, 1, _item(f"{r.stock:,}", right=True))
