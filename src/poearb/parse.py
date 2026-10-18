"""Pure text parsing of OCR output (no image or tesseract dependency): ratios, stock,
gold fees, trade-table rows and currency-name matching."""

import re

from rapidfuzz import fuzz, process

from .models import TradeRow

# Characters OCR commonly returns in place of digits inside numeric tokens.
_DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "|": "1", "!": "1", "]": "1", "[": "1", "O": "0", "o": "0"})
_NUM = r"\d[\d,]*(?:\.\d+)?"
_RATIO_RE = re.compile(rf"({_NUM})\s*[:;]+\s*({_NUM})")
_ROW_RE = re.compile(rf"^\s*([<>‹›«»(]+)?\s*({_NUM})\s*[:;]+\s*({_NUM})\s+(\d[\d,]*)\s*$")
_ROW_LOOSE_RE = re.compile(rf"^\s*(?:[<>‹›«»(]+)?\s*1\s*[7lI|!;:.,]\s*({_NUM})\s+(\d[\d,]*)\s*$")
_TIER_BADGE_RE = re.compile(r"^[Il|1!]{1,3}$")


_DIGIT_FIX_KEEP_MARKERS = str.maketrans({"O": "0", "o": "0"})


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def fix_digits(text: str) -> str:
    """Normalise lookalike characters to digits in a token that should be numeric."""
    return text.translate(_DIGIT_FIX)


def parse_ratio(text: str) -> tuple[float, float] | None:
    """'1 : 2100', '1.50 : 1', '1,981:1' -> (left, right). None if no ratio is present."""
    m = _RATIO_RE.search(fix_digits(text))
    return (_num(m.group(1)), _num(m.group(2))) if m else None


def parse_int(text: str) -> int | None:
    """'1,500' -> 1500. Takes the last numeric token (a coin icon may precede the number)."""
    tokens = re.findall(r"[\d,lI|!\]OS]+", text)
    for tok in reversed(tokens):
        digits = fix_digits(tok).replace(",", "").replace("S", "5")
        if digits.isdigit():
            return int(digits)
    return None


def _stock(token: str) -> int:
    """'1,981' -> 1981. Thousands groups are exactly 3 digits, so drop stray trailing OCR junk
    (e.g. '1,9811' from '1,981]')."""
    if "," in token:
        head, *groups, last = token.split(",")
        token = ",".join([head, *groups, last[:3]])
    return int(token.replace(",", ""))


def parse_trade_row(line: str) -> TradeRow | None:
    """'< 1 : 2297   25' -> TradeRow((1, 2297), 25, '<'). None if the line isn't a row."""
    # Fix lookalikes only after the optional marker, so '<' / '(' are not mistaken for digits.
    m = re.match(r"^\s*([<>‹›«»(]+)?(.*)$", line)
    marker, rest = m.group(1), fix_digits(m.group(2))
    r = _ROW_RE.match(rest)
    if not r:
        # A table row always reads "1 : price": tesseract sometimes turns the colon into a
        # look-alike glyph (or a 7), which glues it to the price ("1:3780" -> "173780").
        loose = _ROW_LOOSE_RE.match(line.translate(_DIGIT_FIX_KEEP_MARKERS))
        if not loose:
            return None
        agg = None
        if marker:
            agg = "<" if marker[0] in "<‹«(" else ">"
        return TradeRow((1.0, _num(loose.group(1))), _stock(loose.group(2)), agg)
    agg = None
    if marker:
        agg = "<" if marker[0] in "<‹«(" else ">"
    return TradeRow((_num(r.group(2)), _num(r.group(3))), _stock(r.group(4)), agg)


def _digits(x: float) -> str:
    return f"{x:g}".replace(".", "")


def _dot_moves(x: float) -> list[float]:
    """The prices with the same digits as `x` and the decimal point somewhere else
    ("714" -> 7.14, 71.4, 714, 7140; "18.43" -> 1.843, 18.43, 184.3, 1843)."""
    digits = _digits(x).lstrip("0")
    if not digits or "e" in digits:
        return []
    return [float(f"{digits[:i]}.{digits[i:]}") if i else float(f"0.{digits}") for i in range(len(digits))] + [
        float(digits)
    ]


def repair_table(rows: list[TradeRow]) -> list[TradeRow]:
    """Fix a misplaced decimal point using what a trades table must look like.

    OCR drops faint points ("7.14" read as "714") and sometimes invents one ("1843" read as
    "18.43"), which moves a price by a factor of 10 or 100 against its neighbours. Prices in one
    table sit close together, so a price more than 30x away from the typical one whose digits fit
    a decimal-point position near the table's typical price (the median; for tables of fewer
    than 3 rows, the row above) is moved there. The summary row (`<` / `>`) repeats
    the ratio of the row above it. A price that cannot be explained this way (such as the real
    jump to "1 : 1" in a thin market) is left as read.
    """
    def price(r: TradeRow) -> float:
        return r.ratio[1] / r.ratio[0]

    def fixed(r: TradeRow, ref: float) -> TradeRow:
        v = price(r)
        if r.ratio[0] != 1.0 or ref <= 0:
            return r
        if r.aggregate and v != ref and _digits(v) == _digits(ref):
            v = ref
        elif not ref / 30 <= v <= ref * 30:
            near = [c for c in _dot_moves(v) if ref / 3 <= c <= ref * 3]
            if near:
                v = min(near, key=lambda c: abs(c - ref))
        return r if v == price(r) else TradeRow((1.0, v), r.stock, r.aggregate)

    prices = sorted(price(r) for r in rows)
    median = prices[len(prices) // 2] if len(prices) >= 3 else None
    out: list[TradeRow] = []
    for r in rows:
        ref = median if median is not None else (price(out[-1]) if out else None)
        if r.aggregate and out:  # the summary row repeats the row above, whatever the median says
            ref = price(out[-1])
        out.append(fixed(r, ref) if ref is not None else r)
    if median is None and len(out) == 2:  # two rows: the first has nothing above, check it against the second
        out[0] = fixed(out[0], price(out[1]))
    return out


def match_name(text: str, names: list[str], threshold: float = 80.0) -> tuple[str | None, float]:
    """Fuzzy-match OCR'd item-name text to a known name; (None, score) below `threshold`.

    The crop also catches the item icon and the favourite star, which OCR turns into junk
    tokens next to the name, so every contiguous run of tokens is tried and the best match wins.
    """
    tokens = [t for t in re.split(r"\s+", text.replace("’", "'")) if t and not _TIER_BADGE_RE.match(t)]
    # The whole text first: a sub-run could match a shorter, different name ("Exalted Orb"
    # inside "Perfect Exalted Orb"), so sub-runs only count when the whole text fails.
    best: tuple[str | None, float] = (None, 0.0)
    n = len(tokens)
    for length in range(n, 0, -1):
        for i in range(n - length + 1):
            cleaned = " ".join(tokens[i : i + length]).strip()
            if len(cleaned) < 3:
                continue
            hit = process.extractOne(cleaned, names, scorer=fuzz.ratio, score_cutoff=threshold)
            if hit and hit[1] > best[1]:
                best = (hit[0], hit[1])
        if best[0] is not None and length == n:
            break
    return best


def check_gold_fee(
    gold_fee: int | None, want_cur: str | None, want_qty: int | None, gold_cost: dict[str, int]
) -> str | None:
    """Return a warning if the read fee disagrees with per-item cost * want quantity."""
    if None in (gold_fee, want_qty) or want_cur not in gold_cost:
        return None
    expected = gold_cost[want_cur] * want_qty
    if expected != gold_fee:
        return f"gold fee {gold_fee} != expected {expected} ({gold_cost[want_cur]} x {want_qty})"
    return None
