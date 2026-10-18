from poearb import store
from poearb.models import Offer


def _offer(want_qty, fee):
    return Offer("Chaos Orb", 1, "Exalted Orb", want_qty, fee, 99.0)


def test_gold_cost_confirmed_when_consistent(tmp_path):
    con = store.connect(str(tmp_path / "t.sqlite"))
    for qty in (1, 5, 10):
        store.add_offer(con, _offer(qty, 100 * qty))
    est = store.gold_cost_estimates(con, confirm_n=3)["Exalted Orb"]
    assert est["per_item_gold"] == 100
    assert est["confirmed"]


def test_gold_cost_not_confirmed_when_inconsistent(tmp_path):
    con = store.connect(str(tmp_path / "t.sqlite"))
    for qty, fee in ((1, 100), (5, 500), (10, 990)):
        store.add_offer(con, _offer(qty, fee))
    assert not store.gold_cost_estimates(con)["Exalted Orb"]["confirmed"]


def test_add_scan_stores_rows_and_feeds_gold_learner(tmp_path):
    from poearb.models import ScanResult, TradeRow

    con = store.connect(str(tmp_path / "t.sqlite"))
    r = ScanResult(
        want_cur="Regal Orb", have_cur="Exalted Orb", market_ratio=(40.0, 1.0),
        want_qty=40, have_qty=1, gold_fee=4800, confidence=80.0,
        available=[TradeRow((1, 2100), 1), TradeRow((1, 2297), 25, "<")],
        competing=[TradeRow((1, 1900), 46968, ">")],
    )
    sid = store.add_scan(con, r, "x.png")
    assert con.execute("SELECT COUNT(*) FROM trade_rows WHERE scan_id=?", (sid,)).fetchone()[0] == 3
    assert store.gold_cost_estimates(con)["Regal Orb"]["per_item_gold"] == 120
