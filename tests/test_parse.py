import pytest

from poearb import parse
from poearb.models import TradeRow


@pytest.mark.parametrize("text,expected", [
    ("1 : 2100", (1, 2100)),
    ("1.50 : 1", (1.5, 1)),
    ("452:1", (452, 1)),
    ("1:: 2100", (1, 2100)),
    ("l : 60", (1, 60)),
    ("3 ; 1", (3, 1)),
    ("1 : 3,975", (1, 3975)),
    ("no ratio here", None),
])
def test_parse_ratio(text, expected):
    assert parse.parse_ratio(text) == expected


@pytest.mark.parametrize("text,expected", [("1,500", 1500), ("4,800", 4800), ("& 120", 120), ("20", 20), ("", None)])
def test_parse_int(text, expected):
    assert parse.parse_int(text) == expected


@pytest.mark.parametrize("line,expected", [
    ("1: 2100 1", TradeRow((1, 2100), 1)),
    ("1: 1981 1,98]", TradeRow((1, 1981), 1981)),
    ("1: 1974 1,9744", TradeRow((1, 1974), 1974)),
    ("«1: 2297 25", TradeRow((1, 2297), 25, "<")),
    ("<1: 2297 25", TradeRow((1, 2297), 25, "<")),
    ("> 1: 1900 46,968", TradeRow((1, 1900), 46968, ">")),
    ("1: 1900 l", TradeRow((1, 1900), 1)),
    ("Ratio Stock", None),
    ("1 : 1", None),
])
def test_parse_trade_row(line, expected):
    assert parse.parse_trade_row(line) == expected


def test_match_name():
    names = ["Orb of Augmentation", "Greater Orb of Augmentation", "Divine Orb", "Regal Orb"]
    assert parse.match_name("Greater Orb of II Augmentation", names)[0] == "Greater Orb of Augmentation"
    assert parse.match_name("Orb of Augmentation", names)[0] == "Orb of Augmentation"
    assert parse.match_name("Divne Orb", names)[0] == "Divine Orb"
    assert parse.match_name("zzzz", names)[0] is None


def test_check_gold_fee():
    gc = {"Regal Orb": 120}
    assert parse.check_gold_fee(4800, "Regal Orb", 40, gc) is None
    assert "!=" in parse.check_gold_fee(4000, "Regal Orb", 40, gc)
    assert parse.check_gold_fee(None, "Regal Orb", 40, gc) is None


def test_match_name_ignores_icon_and_star_junk():
    from poearb.parse import match_name

    names = ["Divine Orb", "Orb of Alchemy", "Exalted Orb"]
    assert match_name("i'fi%& Divine Orb *", names)[0] == "Divine Orb"
    assert match_name("Exalted Orb", names)[0] == "Exalted Orb"
    assert match_name("% & *", names)[0] is None


@pytest.mark.parametrize("line,expected", [
    ("173780 2", TradeRow((1, 3780), 2)),            # ':' read as '7' and glued to the price
    ("1;3780 2", TradeRow((1, 3780), 2)),
    ("<1,2296 27", TradeRow((1, 2296), 27, "<")),
    ("1:7.14 7", TradeRow((1, 7.14), 7)),             # a proper reading is untouched
    ("12 5", None),                                    # not a trade row
])
def test_parse_trade_row_with_a_garbled_colon(line, expected):
    assert parse.parse_trade_row(line) == expected
