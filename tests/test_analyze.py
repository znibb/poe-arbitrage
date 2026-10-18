from poearb.analyze import plan_scans, round_trips


def _rows(*items):
    return [{"name": n, "price": p} for n, p in items]


def test_one_scan_per_pair_core_pairs_first_more_valuable_side_wanted():
    steps = plan_scans(_rows(("Divine Orb", 1), ("Mirror of Kalandra", 800), ("Orb of Alchemy", 0.0005)))
    assert [s.pair for s in steps[:3]] == [
        ("Chaos Orb", "Exalted Orb"), ("Divine Orb", "Exalted Orb"), ("Divine Orb", "Chaos Orb"),
    ]
    assert len(steps) == 3 + 2 * 3  # Divine Orb is a core, so it is not an item
    assert steps[3].pair == ("Mirror of Kalandra", "Exalted Orb")
    assert steps[6].pair == ("Exalted Orb", "Orb of Alchemy")  # cheaper than an exalt: exalt is wanted


def test_plan_has_no_duplicates_and_covers_every_round_trip_either_orientation():
    steps = plan_scans(_rows(("X", 5), ("X", 5), ("Y", 5)))
    pairs = [frozenset(s.pair) for s in steps]
    assert len(pairs) == len(set(pairs))
    for a, item, b in round_trips("X"):
        assert {frozenset((item, a)), frozenset((b, item)), frozenset((a, b))} <= set(pairs)


def test_core_prices_override_and_max_items():
    steps = plan_scans(_rows(("A", 1), ("B", 1), ("C", 1)), max_items=1,
                       core_prices={"Chaos Orb": 2.0})
    assert len(steps) == 6
    assert steps[0].pair == ("Chaos Orb", "Exalted Orb")


# ---- profitability -------------------------------------------------------------------------
import pytest  # noqa: E402

from poearb.analyze import Book, book_from_scan, evaluate_trip, levels, rank_trips, walk  # noqa: E402
from poearb.models import ScanResult, TradeRow  # noqa: E402

EX, DIV = "Exalted Orb", "Divine Orb"
PRICES = {DIV: 1.0, EX: 1 / 700, "Chaos Orb": 0.095}
GOLD = {"X": 100, "Y": 1, DIV: 50, EX: 1}


def _books(x="X", x_asks=((1000, 3),), x_ex_bids=(), x_div_bids=((2, 3),), div_ex_bids=((690, 5),)):
    """Div/Ex, X/Ex and X/Div books; bids are (p, qty in want)."""
    return {
        frozenset((DIV, EX)): Book(DIV, EX, [(700, 10)], list(div_ex_bids)),
        frozenset((x, EX)): Book(x, EX, list(x_asks), list(x_ex_bids)),
        frozenset((x, DIV)): Book(x, DIV, [], list(x_div_bids)),
    }


def test_book_from_scan_converts_competing_stock_to_want_units():
    res = ScanResult(
        want_cur="X", have_cur=EX,
        available=[TradeRow((1.0, 1000.0), 3)],
        competing=[TradeRow((1.0, 900.0), 1800), TradeRow((1.0, 950.0), 950)],
    )
    book = book_from_scan(res, ts=1.0)
    assert book.asks == [(1000.0, 3.0)]
    assert book.bids == [(950.0, 1.0), (900.0, 2.0)]  # best price first; 1800 EX / 900 = 2 X
    assert levels(book, EX, "X") == [(1 / 1000.0, 3000.0)]  # buying: 1000 EX per X, 3 X deep
    assert levels(book, "X", EX) == [(950.0, 1.0), (900.0, 2.0)]


def test_walk_floors_to_whole_units_and_respects_depth():
    assert walk([(1 / 1000, 3000)], 2500) == (2, 2000.0)  # 2.5 X -> 2 X, only 2000 EX spent
    assert walk([(1 / 1000, 3000)], 99999) == (3, 3000.0)  # capped by the 3 X on offer
    assert walk([(1.6, 3), (1.0, 5)], 2) == (3, 2.0)  # 3.2 -> 3 units, paid for in whole units (1.875 -> 2)
    assert walk([(535.0, 0.2)], 1) == (0, 0.0)  # a level too small for one whole item is unusable
    assert walk([], 10) == (0, 0.0)


def test_profitable_triangle_hand_computed():
    # 2 X cost 2000 EX; they sell for 4 DIV; 4 DIV sell for 4*690 = 2760 EX -> +760.
    # 3 X would give 6 DIV but only 5 DIV are bid on the Div/Ex book (3450 - 3000 = +450).
    r = evaluate_trip(EX, "X", DIV, _books(), GOLD, PRICES)
    assert r.route == (EX, "X", DIV)
    assert r.start == 2000 and r.profit == 760
    assert r.margin_pct == pytest.approx(38.0)
    assert r.gold == 2 * 100 + 4 * 50 + 2760 * 1
    assert r.profit_div == pytest.approx(760 / 700)
    assert r.max_profitable_start == 3000  # still profitable at depth 3 X (+450)
    assert not r.notes


def test_losing_triangle_has_negative_profit():
    r = evaluate_trip(EX, "X", DIV, _books(x_div_bids=((1, 3),)), GOLD, PRICES)  # X only fetches 1 DIV
    assert r.profit < 0 and r.max_profitable_start is None


def test_no_liquidity_or_missing_book_gives_none():
    assert evaluate_trip(EX, "X", DIV, _books(x_asks=()), GOLD, PRICES) is None  # nobody sells X
    books = _books()
    del books[frozenset(("X", DIV))]
    assert evaluate_trip(EX, "X", DIV, books, GOLD, PRICES) is None


def test_missing_gold_cost_is_noted():
    r = evaluate_trip(EX, "X", DIV, _books(), {DIV: 50, EX: 1}, PRICES)
    assert "no gold cost for X" in r.notes


def test_ranking_modes_differ():
    books = {**_books("X"), **_books("Y", x_asks=((1000, 1),))}
    items = ["X", "Y"]
    by_profit, evaluable, total = rank_trips(items, books, GOLD, PRICES, ignore_gold=True,
                                             cores=(EX, DIV))
    assert (evaluable, total) == (4, 4)  # Div -> item -> Ex needs asks that were not scanned
    assert [r.route[1] for r in by_profit if r.profit > 0] == ["X", "Y"]  # X earns 760, Y 380
    by_gold, *_ = rank_trips(items, books, GOLD, PRICES, ignore_gold=False, cores=(EX, DIV))
    assert [r.route[1] for r in by_gold if r.profit > 0] == ["Y", "X"]  # Y is cheaper in gold


def test_a_bid_for_less_than_one_item_is_not_a_trade():
    """Regression: a misread '535' (really 5.35) bid of 107 Divine looked like 0.2 items for 107
    Divine, and selling 0.2 item for 107 Divine produced a +5134 Chaos 'profit'."""
    CH = "Chaos Orb"
    books = {
        frozenset((CH, "PC")): Book("PC", CH, [(55, 3)], []),
        frozenset(("PC", DIV)): Book("PC", DIV, [], [(535, 107 / 535), (6, 8682 / 6)]),
        frozenset((DIV, CH)): Book(DIV, CH, [(8.8, 50)], [(8.75, 50)]),
    }
    r = evaluate_trip(CH, "PC", DIV, books, {}, {DIV: 1.0, CH: 0.11})
    assert r.profit < 0  # 55 Chaos -> 1 PC -> 6 Divine -> 52 Chaos


def test_repair_table_fixes_misplaced_decimal_points():
    from poearb.parse import repair_table

    def rows(prices, agg=None):
        return [TradeRow((1.0, p), 1, agg if i == len(prices) - 1 else None) for i, p in enumerate(prices)]

    def prices(tbl):
        return [r.ratio[1] for r in tbl]

    # dropped points: 714 -> 7.14 (also on the summary row), 535 -> 5.35
    assert prices(repair_table(rows([6.5, 7.0, 7.14, 714.0], "<"))) == [6.5, 7.0, 7.14, 7.14]
    assert prices(repair_table(rows([6.0, 535.0, 5.2, 4.25, 4.0, 4.0], ">"))) == [6.0, 5.35, 5.2, 4.25, 4.0, 4.0]
    # an invented point: 1843 read as 18.43
    assert prices(repair_table(rows([1850.0, 18.43, 1830.0, 1830.0], ">"))) == [1850.0, 1843.0, 1830.0, 1830.0]
    # the first row is checked against the second
    assert prices(repair_table(rows([650.0, 6.67, 6.7]))) == [6.5, 6.67, 6.7]
    # real jumps and untouched tables stay as they are (even the drop to "1 : 1" in a thin market)
    for tbl in (rows([1750.0, 1500.0, 92.0, 90.0, 80.0, 80.0], ">"), rows([3400.5, 3780.0, 5800.0, 5900.0]),
                rows([2550.0, 2500.0, 1600.0, 1575.0, 1.0, 1.0], ">")):
        assert repair_table(tbl) == tbl


def test_ocr_vote_prefers_the_reading_with_the_decimal_point():
    from poearb.ocr import choose

    valid = lambda t: True  # noqa: E731
    votes = {"1:714 7": [90.0, 88.0, 90.0], "1:7.14 7": [80.0, 82.0]}
    assert choose(votes, valid)[0] == "1:7.14 7"
    assert choose({"1:1843 1,843": [90.0, 88.0], "1:18.43 1,843": [80.0]}, valid)[0] == "1:1843 1,843"  # point seen once
    assert choose({".   1:6": [70.0], ". 1:6": [72.0], "1:6": [75.0], "7 1:0": [90.0]}, valid)[0] == "1:6"  # spacing/junk
    assert choose({"1:714 7": [90.0]}, valid)[0] == "1:714 7"  # nothing to prefer
    assert choose({"1:6 8,682": [90.0], "1.6 8,682": [70.0]}, valid)[0] == "1:6 8,682"  # different texts


def test_very_high_margin_is_flagged():
    r = evaluate_trip(EX, "X", DIV, _books(x_div_bids=((200, 3),)), GOLD, PRICES)
    assert any("margin over 100%" in n for n in r.notes)
