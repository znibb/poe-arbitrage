"""GUI smoke tests under the offscreen Qt platform (no display needed)."""

import os
import signal
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from poearb.gui.main_window import MainWindow, parse_top_n  # noqa: E402
from poearb.gui.sigbridge import Sigusr1Bridge  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Windows save settings to config.local.yaml; keep that away from the real project."""
    from poearb import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)


def _select_league(win, league="L"):
    win.league_combo.addItem(league, league)
    win.league_combo.setEnabled(True)
    win.league_combo.setCurrentIndex(0)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("text,expected", [
    ("20", 20), (" 5 ", 5), ("500", 500), ("1", 1),
    ("0", None), ("-3", None), ("abc", None), ("", None), ("2.5", None), ("1_0", None),
    ("501", None), ("٣", None),
])
def test_parse_top_n(text, expected):
    assert parse_top_n(text) == expected


def test_invalid_n_shows_inline_error_and_does_not_fetch(app, monkeypatch):
    win = MainWindow({"ninja": {"top_n": 20}})
    _select_league(win)
    calls = []
    monkeypatch.setattr(win, "_run", lambda *a, **k: calls.append(a))
    win.n_edit.setText("abc")
    win.check_volume()
    assert not win.n_error.isHidden() and calls == []
    win.n_edit.setText("5")
    win.check_volume()
    assert win.n_error.isHidden() and len(calls) == 1


def test_volume_result_fills_table_and_timestamp(app):
    win = MainWindow({"ninja": {"top_n": 20}})
    rows = [{"name": "Chaos Orb", "category": "Currency", "volume": 78640, "price": 0.09375,
             "max_volume_currency": "divine"}]
    win._volume_done({"rows": rows, "league": "L", "fetched_at": time.time(), "from_cache": True, "stale": False})
    assert win.volume_table.rowCount() == 1 and win.volume_table.item(0, 1).text() == "Chaos Orb"
    assert "poe.ninja data:" in win.data_status.text() and "cached" in win.data_status.text()


def test_scan_without_region_reports_and_does_not_start(app):
    win = MainWindow({"capture": {"region": None}})
    win.start_scan()
    assert "Calibrate" in win.scan_status.text() and not win._scanning


def test_sigusr1_reaches_qt_signal(app):
    bridge = Sigusr1Bridge()
    got = []
    bridge.triggered.connect(lambda: got.append(1))
    os.kill(os.getpid(), signal.SIGUSR1)
    deadline = time.time() + 2
    while not got and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    bridge.close()
    assert got == [1]


def _selector(app, initial):
    from PySide6.QtGui import QGuiApplication, QImage

    from poearb.gui.region_selector import RegionSelector

    screen = QGuiApplication.primaryScreen()
    size = screen.geometry().size() * screen.devicePixelRatio()
    img = QImage(size, QImage.Format.Format_RGB888)
    img.fill(0)
    return RegionSelector(img, screen, initial)


def test_region_selector_roundtrips_initial_region(app):
    initial = {"x": 100, "y": 50, "w": 300, "h": 200}
    assert _selector(app, initial).region() == initial


def test_region_selector_enter_accepts_and_escape_cancels(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    sel = _selector(app, {"x": 10, "y": 10, "w": 200, "h": 100})
    got = []
    sel.accepted.connect(got.append)
    sel.show()
    QTest.keyClick(sel, Qt.Key.Key_Return)
    assert got == [{"x": 10, "y": 10, "w": 200, "h": 100}]

    sel = _selector(app, None)
    cancelled = []
    sel.cancelled.connect(lambda: cancelled.append(1))
    sel.show()
    QTest.keyClick(sel, Qt.Key.Key_Escape)
    assert cancelled == [1]


def _rows(*names):
    return [{"name": n, "category": "Currency", "volume": 100 - i, "price": 1.0, "max_volume_currency": ""}
            for i, n in enumerate(names)]


def test_plan_after_volume_check_and_marking_done(app):
    from poearb.models import ScanResult, TradeRow

    win = MainWindow({"ninja": {"top_n": 20}})
    win._volume_done({"rows": _rows("Divine Orb", "Orb of Alchemy"), "league": "L",
                      "fetched_at": time.time(), "from_cache": True, "stale": False})
    assert win.plan_table.rowCount() == 3 + 3  # core pairs + the one non-core item vs each core
    assert win.plan_table.item(0, 4).text() == "<- next"
    first = win._plan[0]  # reversed orientation still ticks it off
    res = ScanResult(want_cur=first.have, have_cur=first.want, market_ratio=(1.0, 3.0),
                     available=[TradeRow((1.0, 3.0), 5)])
    win._mark_plan_done(res)
    assert win.plan_table.item(0, 4).text().endswith("1 : 3")
    assert win.plan_table.item(1, 4).text() == "<- next"


def test_ignore_mirror_checkbox(app, monkeypatch):
    win = MainWindow({"ninja": {"top_n": 20}})
    _select_league(win)
    calls = []
    monkeypatch.setattr(win, "_run", lambda *a, **k: calls.append(a))
    assert win.ignore_mirror.text() == "Ignore Mirror of Kalandra" and win.ignore_mirror.isChecked()
    win.check_volume()
    assert calls[-1][-2] == ("Mirror of Kalandra", "Hinekora's Lock")
    win.ignore_mirror.setChecked(False)  # nothing shown yet, so no automatic re-run
    assert len(calls) == 1
    win.check_volume()
    assert calls[-1][-2] == ("Hinekora's Lock",)
    win._volume_done({"rows": _rows("Chaos Orb"), "league": "L", "fetched_at": time.time(),
                      "from_cache": True, "stale": False})
    win.ignore_mirror.setChecked(True)  # results showing: re-check applies the change
    assert len(calls) == 3


def test_fetch_volume_filters_ignored_without_shrinking_n(monkeypatch):
    from poearb.gui import main_window as mw

    rows = [{"name": n} for n in ("Divine Orb", "Mirror of Kalandra", "Chaos Orb", "A", "Exalted Orb", "B", "C")]

    class C:
        data = {"core": {"rates": {}}}
        fetched_at, from_cache, stale = 1.0, True, False

    monkeypatch.setattr(mw.ninja, "get_all_overviews", lambda *a, **k: {"Currency": C()})
    monkeypatch.setattr(mw.ninja, "top_by_volume", lambda ov, n: rows[:n])
    out = mw.fetch_volume({"ninja": {}}, 2, ("Mirror of Kalandra",), "L")
    assert [r["name"] for r in out["rows"]] == ["A", "B"]  # no core orbs, and N items are still listed


def test_ignore_lock_checkbox_and_no_offers_marks_plan(app, monkeypatch):
    from poearb.models import ScanResult, TradeRow

    win = MainWindow({"ninja": {"top_n": 20}})
    _select_league(win)
    calls = []
    monkeypatch.setattr(win, "_run", lambda *a, **k: calls.append(a))
    assert win.ignore_lock.text() == "Ignore Hinekora's Lock" and win.ignore_lock.isChecked()
    win.check_volume()
    assert calls[-1][-2] == ("Mirror of Kalandra", "Hinekora's Lock")
    win.ignore_mirror.setChecked(False)
    win.check_volume()
    assert calls[-1][-2] == ("Hinekora's Lock",)

    win._volume_done({"rows": _rows("Orb of Alchemy"), "league": "L", "fetched_at": time.time(),
                      "from_cache": True, "stale": False})
    first = win._plan[0]
    win._mark_plan_done(ScanResult(want_cur=first.want, have_cur=first.have,
                                   competing=[TradeRow((1.0, 5.0), 5)]))
    assert win.plan_table.item(0, 4).text().endswith("no offers")


def test_top_n_is_saved_when_checking_volume_and_restored_on_start(app, monkeypatch, tmp_path):
    from poearb import config

    win = MainWindow(config.load_config())
    _select_league(win)
    monkeypatch.setattr(win, "_run", lambda *a, **k: None)
    win.n_edit.setText("42")
    win.check_volume()
    assert config.load_config()["ninja"]["top_n"] == 42
    assert MainWindow(config.load_config()).n_edit.text() == "42"
    win.n_edit.setText("abc")  # invalid input must not overwrite it
    win.check_volume()
    assert config.load_config()["ninja"]["top_n"] == 42


def _scan(want, have, asks=(), bids=()):
    from poearb.models import ScanResult, TradeRow

    return ScanResult(
        want_cur=want, have_cur=have,
        available=[TradeRow((1.0, float(p)), q) for p, q in asks],
        competing=[TradeRow((1.0, float(p)), int(p * q)) for p, q in bids],
    )


def _feed_books(win):
    win._gold_cost = {"X": 100, "Y": 1, "Divine Orb": 50, "Exalted Orb": 1}
    win._volume_done({"rows": _rows("X", "Y"), "league": "L", "fetched_at": time.time(), "from_cache": True,
                      "stale": False, "core_prices": {"Divine Orb": 1.0, "Exalted Orb": 1 / 700}})
    for st in win._plan:  # scan every planned pair (the exact book contents are set below)
        assert st.pair  # pairs exist for all items and core pairs
    win._mark_plan_done(_scan("Divine Orb", "Exalted Orb", asks=[(700, 10)], bids=[(690, 5)]))
    win._mark_plan_done(_scan("X", "Exalted Orb", asks=[(1000, 3)]))
    win._mark_plan_done(_scan("X", "Divine Orb", bids=[(2, 3)]))
    win._mark_plan_done(_scan("Y", "Exalted Orb", asks=[(1000, 1)]))
    win._mark_plan_done(_scan("Y", "Divine Orb", bids=[(2, 3)]))


def test_profit_panel_ranks_and_gold_checkbox_reorders(app):
    win = MainWindow({"ninja": {"top_n": 20}})
    _feed_books(win)
    t = win.profit_table
    trips = [t.item(i, 1).text() for i in range(t.rowCount())]
    assert trips[0] == "Exalted > X > Divine > Exalted"  # profit 760 vs 380
    assert t.item(0, 3).text() == "+760 Exalted"
    assert "have all three pairs scanned; ranked by profit." in win.profit_info.text()
    win.ignore_gold.setChecked(False)  # per 1000 gold: Y is cheaper in gold per profit
    assert t.item(0, 1).text() == "Exalted > Y > Divine > Exalted"
    assert "profit per 1000 gold" in win.profit_info.text()


def test_checkbox_states_default_checked_saved_and_restored(app):
    from poearb import config

    win = MainWindow(config.load_config())
    assert win.ignore_mirror.isChecked() and win.ignore_lock.isChecked() and win.ignore_gold.isChecked()
    win.ignore_gold.setChecked(False)
    win.ignore_lock.setChecked(False)
    saved = config.load_config()["gui"]
    assert saved == {"ignore_mirror": True, "ignore_lock": False, "ignore_exalted": False,
                     "ignore_gold_cost": False}
    again = MainWindow(config.load_config())
    assert again.ignore_mirror.isChecked() and not again.ignore_lock.isChecked() and not again.ignore_gold.isChecked()


def test_scanning_status_is_highlighted_until_done(app):
    from poearb.gui.main_window import BUSY, MUTED

    win = MainWindow({"ninja": {"top_n": 20}})
    win._set_scan_status("Scanning...", busy=True)
    assert win.scan_status.styleSheet() == BUSY
    win._set_scan_status("Last scan: 12:00:00")
    assert win.scan_status.styleSheet() == MUTED


LEAGUES = {"leagues": [("Forbidden Rites", "Forbidden Rites"), ("Standard", "Standard")],
           "fetched_at": time.time(), "from_cache": True, "stale": False}


def test_check_volume_needs_a_selected_league(app, monkeypatch):
    from poearb import config

    win = MainWindow(config.load_config())
    calls = []
    monkeypatch.setattr(win, "_run", lambda *a, **k: calls.append(a))
    assert not win.volume_btn.isEnabled() and not win.league_combo.isEnabled()
    win.check_volume()  # e.g. Enter in the Top N field
    assert calls == []
    win._leagues_done(LEAGUES)  # populated, but nothing chosen yet
    assert [win.league_combo.itemText(i) for i in range(2)] == ["Forbidden Rites", "Standard"]
    assert win.league_combo.currentIndex() == -1 and not win.volume_btn.isEnabled()
    win.league_combo.setCurrentIndex(1)
    assert win.volume_btn.isEnabled()
    win.check_volume()
    assert calls[-1][-1] == "Standard"  # the selected league is what gets fetched
    assert not win.volume_btn.isEnabled()  # greyed out while it runs
    win._volume_done({"rows": _rows("A"), "league": "Standard", "fetched_at": time.time(),
                      "from_cache": True, "stale": False})
    assert win.volume_btn.isEnabled()


def test_league_is_remembered_and_changing_it_resets_scans(app):
    from poearb import config

    win = MainWindow(config.load_config())
    win._leagues_done(LEAGUES)
    win.league_combo.setCurrentIndex(0)
    assert config.load_config()["ninja"]["league"] == "Forbidden Rites"
    win._volume_done({"rows": _rows("X"), "league": "Forbidden Rites", "fetched_at": time.time(),
                      "from_cache": True, "stale": False})
    win._mark_plan_done(_scan("Divine Orb", "Exalted Orb", asks=[(700, 10)]))
    assert win._books and win.plan_table.rowCount()
    win.league_combo.setCurrentIndex(1)  # other economy: nothing from the old one may linger
    assert not win._books and win.plan_table.rowCount() == 0 and win.volume_table.rowCount() == 0

    again = MainWindow(config.load_config())  # a restart: the saved league is there and usable at once
    assert again.selected_league() == "Standard" and again.volume_btn.isEnabled()
    assert again.league_combo.isEnabled()
    again._leagues_done(LEAGUES)  # Fetch leagues fills in the rest and keeps the selection
    assert again.league_combo.count() == 2 and again.selected_league() == "Standard"


def test_top_bar_spans_the_window_in_the_requested_order(app):
    win = MainWindow({"ninja": {"top_n": 20}})
    win.resize(1500, 800)
    win.show()
    bar = [win.leagues_btn, win.league_combo, win.n_edit, win.volume_btn, win.ignore_mirror,
           win.ignore_lock, win.ignore_gold, win.calibrate_btn]
    xs = [w.mapTo(win, w.rect().topLeft()).x() for w in bar]
    assert xs == sorted(xs)
    assert xs[0] < 30 and win.width() - (xs[-1] + win.calibrate_btn.width()) < 30  # edge to edge
    assert win.leagues_btn.text() == "Fetch leagues"


def test_trade_tables_fit_exactly_six_rows(app):
    win = MainWindow({"ninja": {"top_n": 20}})
    for t in (win.avail_table, win.comp_table):
        needed = t.horizontalHeader().height() + 6 * t.verticalHeader().defaultSectionSize()
        assert needed <= t.height() <= needed + 12


def test_ignore_exalted_leaves_chaos_and_divine_as_the_core_pool(app):
    from poearb import config

    win = MainWindow(config.load_config())
    assert win.ignore_exalted.text() == "Ignore Exalted Orb" and not win.ignore_exalted.isChecked()
    _feed_books(win)
    assert win.plan_table.rowCount() == 3 + 2 * 3  # three core pairs, two items vs three cores
    assert "Exalted / Chaos / Divine" in win.plan_box.title()

    win.ignore_exalted.setChecked(True)  # no fetch: the plan is rebuilt from the last result
    pairs = {(win.plan_table.item(i, 1).text(), win.plan_table.item(i, 2).text())
             for i in range(win.plan_table.rowCount())}
    assert win.plan_table.rowCount() == 1 + 2 * 2  # Chaos/Divine, then each item vs Chaos and Divine
    assert not any("Exalted Orb" in p for p in pairs)
    assert "Chaos / Divine" in win.plan_box.title() and "Exalted" not in win.plan_box.title()
    assert config.load_config()["gui"]["ignore_exalted"] is True
    # trips now run only through Chaos and Divine
    win._mark_plan_done(_scan("Divine Orb", "Chaos Orb", asks=[(10, 10)], bids=[(9, 10)]))
    win._mark_plan_done(_scan("X", "Chaos Orb", asks=[(100, 3)]))
    win._mark_plan_done(_scan("X", "Divine Orb", bids=[(12, 3)]))
    trips = [win.profit_table.item(i, 1).text() for i in range(win.profit_table.rowCount())]
    assert trips and all("Exalted" not in t for t in trips)
    assert "of 4 round trips" in win.profit_info.text()  # 2 items x (Chaos,Divine) orderings

    win.ignore_exalted.setChecked(False)
    assert win.plan_table.rowCount() == 3 + 2 * 3
    assert MainWindow(config.load_config()).ignore_exalted.isChecked() is False
