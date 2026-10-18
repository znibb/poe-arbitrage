"""End-to-end OCR regression over tests/fixtures (skipped without tesseract + English data)."""

import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image

from poearb import ocr, scan
from poearb.config import load_gold_cost

FIX = Path(__file__).parent / "fixtures"
EXPECTED = yaml.safe_load((FIX / "expected.yaml").read_text())


def _tesseract_ready() -> bool:
    if not shutil.which("tesseract"):
        return False
    import pytesseract
    return "eng" in pytesseract.get_languages()


pytestmark = pytest.mark.skipif(not _tesseract_ready(), reason="tesseract with eng data not available")


def _ratio(s):
    return tuple(float(x) for x in str(s).split(":"))


@pytest.fixture(scope="module")
def gold_cost():
    return load_gold_cost()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture(name, gold_cost):
    exp = EXPECTED[name]
    res = scan.scan_image(Image.open(FIX / f"{name}.png"), list(gold_cost), gold_cost)
    assert res.want_cur == exp["want_cur"]
    assert res.have_cur == exp["give_cur"]
    assert res.market_ratio == (_ratio(exp["market_ratio"]) if exp["market_ratio"] else None)
    assert res.gold_per_item == gold_cost[exp["want_cur"]]
    assert not res.warnings
    if "available" in exp:
        got = [(t.ratio[0], t.ratio[1], t.stock, t.aggregate) for t in res.available]
        assert got == [tuple(r) if True else r for r in map(tuple, exp["available"])] or got == [
            (a, b, s, g) for a, b, s, g in map(tuple, exp["available"])
        ]
        got = [(t.ratio[0], t.ratio[1], t.stock, t.aggregate) for t in res.competing]
        assert got == [tuple(r) for r in exp["competing"]]
    else:
        assert (res.want_qty, res.have_qty) == (exp["want_qty"], exp["give_qty"])
        assert res.gold_fee == exp["gold_fee"]


def test_window_screenshot_uses_title_anchor():
    """In the game the trades pane covers the Market Ratio header; the window title anchors instead."""
    img = Image.open(FIX / "currency_exchange_window.png")
    assert ocr.find_anchor(img).layout == "title"
    shifted = img.crop((40, 0, img.width, 700))  # a calibrated region rarely starts at the window edge
    assert ocr.find_anchor(shifted).layout == "title"
    res = scan.scan_image(shifted, list(load_gold_cost()), load_gold_cost())
    assert len(res.available) == 6 and len(res.competing) == 6 and not res.warnings


def test_no_available_trades_is_not_a_warning(gold_cost):
    res = scan.scan_image(Image.open(FIX / "no_available_trades.png"), list(gold_cost), gold_cost)
    assert res.no_offers and not res.warnings and res.market_ratio is None
