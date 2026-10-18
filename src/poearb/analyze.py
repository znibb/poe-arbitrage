"""Arbitrage planning around the three core orbs.

A triangle round trip starts in core orb A, buys item X with A, sells X for another core
orb B, and converts B back into A. Every exchange screen shows both sides of one pair: the
Available Trades (offers you can take) and the Competing Trades (what others bid, i.e. the
same market seen from the other direction). So each unordered pair needs scanning only once:

    X/A  gives both "buy X with A" and "sell X for A"
    X/B  gives both "buy X with B" and "sell X for B"
    A/B  gives both directions of the core conversion

The three core pairs are shared by every item, so a plan is 3 + 3 per item scans, and covers
all six (A, B) round trips of each item.

Reading a scan of "I WANT W, I HAVE H" (rows `1 : p`, p = H per W):
  Available Trades  = asks, people giving W for H, stock counted in W      -> buy W with H
  Competing Trades  = bids, people who have H and want W, stock counted in H -> sell W for H
The aggregate rows (`<` / `>`) are treated as one more level at their ratio with their stock.
Profit of a round trip walks the three books level by level with whole-unit quantities (the
quantity of the currency you WANT in each leg is floored), and the gold fee of a leg is
per_item_gold[currency wanted in that leg] * that quantity.
"""

import math
import time
from dataclasses import dataclass, field
from itertools import combinations, permutations

CORE_ORBS = ("Exalted Orb", "Chaos Orb", "Divine Orb")


@dataclass(frozen=True)
class ScanStep:
    """One exchange screen to scan: want `want`, paying with `have`."""

    want: str
    have: str
    purpose: str

    @property
    def pair(self) -> tuple[str, str]:
        return (self.want, self.have)


# Rough value in divine, used only to pick which side of a pair is "I WANT" when poe.ninja's
# rates are not supplied (the game then shows the ratio as 1 : N with a whole number N).
DEFAULT_CORE_PRICES = {"Divine Orb": 1.0, "Chaos Orb": 0.095, "Exalted Orb": 0.0014}


def plan_scans(
    rows: list[dict],
    cores: tuple[str, ...] = CORE_ORBS,
    max_items: int | None = None,
    core_prices: dict[str, float] | None = None,
) -> list[ScanStep]:
    """Ordered scan checklist for the round trips of the volume-ranked `rows` (highest first).

    One scan per unordered pair: the three core pairs first (shared by every item), then each
    item against each core, in volume order. Core orbs themselves are skipped as items. In every
    step the more valuable currency is I WANT, so the ratio reads 1 : N with N a whole number.
    `core_prices` (divine per unit) and each row's `price` decide which side that is.
    """
    cp = {**DEFAULT_CORE_PRICES, **(core_prices or {})}

    def step(a: str, pa: float, b: str, pb: float, purpose: str) -> ScanStep:
        return ScanStep(a, b, purpose) if pa >= pb else ScanStep(b, a, purpose)

    steps = [
        step(a, cp.get(a, 0.0), b, cp.get(b, 0.0), "core pair (shared)")
        for a, b in combinations(cores, 2)
    ]
    seen = set()
    for r in rows:
        name = r["name"]
        if name in CORE_ORBS or name in cores or name in seen:  # an ignored core is no item either
            continue
        seen.add(name)
        if max_items is not None and len(seen) > max_items:
            break
        price = r.get("price") or 0.0
        steps += [step(name, price, core, cp.get(core, 0.0), f"{name} vs {core}") for core in cores]
    return steps


def round_trips(item: str, cores: tuple[str, ...] = CORE_ORBS) -> list[tuple[str, str, str]]:
    """The (start core A, item, middle core B) round trips A -> item -> B -> A."""
    return [(a, item, b) for a, b in permutations(cores, 2)]


# ---- profitability ---------------------------------------------------------------------

Level = tuple[float, float]  # (units out per unit in, capacity in units of the input currency)


@dataclass
class Book:
    """Both sides of one pair as scanned from "I WANT want, I HAVE have"."""

    want: str
    have: str
    asks: list[tuple[float, float]]  # (p = have per want, qty in want), cheapest first
    bids: list[tuple[float, float]]  # (p, qty in want), highest price first
    ts: float = 0.0
    flagged: bool = False  # the scan had OCR warnings


def book_from_scan(res, ts: float | None = None) -> "Book | None":
    """Book from a ScanResult with trade rows; None if currencies or rows are missing."""
    if not (res.want_cur and res.have_cur and (res.available or res.competing)):
        return None

    def price(row) -> float:
        return row.ratio[1] / row.ratio[0]  # "w : h" -> have per want

    asks = sorted((price(r), float(r.stock)) for r in res.available if r.ratio[0] > 0 and r.ratio[1] > 0)
    bids = sorted(
        ((price(r), r.stock / price(r)) for r in res.competing if r.ratio[0] > 0 and r.ratio[1] > 0),
        reverse=True,
    )
    return Book(res.want_cur, res.have_cur, asks, bids, time.time() if ts is None else ts,
                flagged=bool(res.warnings))


def levels(book: Book, src: str, dst: str) -> list[Level]:
    """Conversion levels src -> dst, best rate first."""
    if (src, dst) == (book.have, book.want):  # buy `want` with `have`: take the asks
        return [(1 / p, p * q) for p, q in book.asks]
    if (src, dst) == (book.want, book.have):  # sell `want` for `have`: take the bids
        return [(p, q) for p, q in book.bids]
    raise ValueError(f"book {book.want}/{book.have} cannot convert {src} -> {dst}")


def walk(lv: list[Level], amount_in: float) -> tuple[int, float]:
    """Spend up to `amount_in` along `lv`; returns (whole units received, units spent).

    Everything is traded in whole units: the input offered to a level is rounded down (you cannot
    sell 0.2 of an item), the units received are rounded down, and the units paid for them are
    rounded up.
    """
    out_total, spent = 0, 0
    remaining = math.floor(amount_in + 1e-9)
    for rate, cap in lv:
        if remaining <= 0:
            break
        take = math.floor(min(remaining, cap) + 1e-9)
        got = math.floor(take * rate + 1e-9)
        if got <= 0:
            continue
        used = min(take, math.ceil(got / rate - 1e-9))
        out_total += got
        spent += used
        remaining -= used
    return out_total, float(spent)


@dataclass
class TripResult:
    route: tuple[str, str, str]  # A, item, B: A -> item -> B -> A
    start: float  # A spent on the first leg
    profit: float  # in A
    profit_div: float
    margin_pct: float
    gold: int  # gold fees over the three legs
    profit_per_1000_gold: float | None
    max_profitable_start: float | None
    oldest_ts: float
    notes: list[str] = field(default_factory=list)


def _trip_legs(a: str, x: str, b: str, books: dict) -> list[list[Level]] | None:
    try:
        return [levels(books[frozenset(p)], *p) for p in ((a, x), (x, b), (b, a))]
    except KeyError:
        return None


def _run(legs: list[list[Level]], start: float) -> tuple[float, float, list[int]]:
    """(final A, A spent on leg 1, whole units wanted per leg)."""
    q1, spent = walk(legs[0], start)
    q2, _ = walk(legs[1], q1)
    q3, _ = walk(legs[2], q2)
    return q3, spent, [q1, q2, q3]


def _candidates(legs: list[list[Level]]) -> list[float]:
    """Start amounts (in A) worth trying: level breakpoints of every leg mapped back to A, and a
    geometric grid up to the depth of the first leg."""
    cands: set[float] = set()
    scale = 1.0  # A per unit of the leg's input currency
    for lv in legs:
        cum = 0.0
        for rate, cap in lv:
            cum += cap
            cands.add(cum * scale)
        if lv:
            scale /= lv[0][0]  # one input unit of the next leg costs 1/rate of this leg's input
    depth = sum(cap for _, cap in legs[0])
    x = 1.0
    while x < depth:
        cands.add(x)
        x *= 2
    cands.add(depth)
    return sorted(c for c in cands if c > 0)


def evaluate_trip(
    a: str, x: str, b: str, books: dict, gold_cost: dict[str, int], core_prices: dict[str, float]
) -> TripResult | None:
    """Best-sized profit of A -> x -> B -> A from `books` (frozenset(pair) -> Book).

    None if a book is missing or no trade can be made at all. The start is the size with the
    highest profit; `max_profitable_start` is the largest tried size that still earns something.
    """
    legs = _trip_legs(a, x, b, books)
    if legs is None:
        return None
    best = None
    max_ok = None
    for start in _candidates(legs):
        final, spent, qty = _run(legs, start)
        if spent <= 0 or final <= 0:
            continue
        profit = final - spent
        if profit > 0:
            max_ok = max(max_ok or 0, spent)
        if best is None or profit > best[0]:
            best = (profit, spent, qty)
    if best is None:
        return None
    profit, spent, qty = best
    notes = []
    gold = 0
    for cur, q in zip((x, b, a), qty):
        if cur in gold_cost:
            gold += gold_cost[cur] * q
        else:
            notes.append(f"no gold cost for {cur}")
    bs = [books[frozenset(p)] for p in ((a, x), (x, b), (b, a))]
    if any(bk.flagged for bk in bs):
        notes.append("a scan had OCR warnings")
    if 100 * profit / spent > 100:
        notes.append("margin over 100%: check the scans for misread numbers")
    profit_div = profit * core_prices.get(a, 0.0)
    return TripResult(
        (a, x, b), spent, profit, profit_div, 100 * profit / spent, gold,
        profit_div * 1000 / gold if gold else None, max_ok, min(bk.ts for bk in bs), notes,
    )


def rank_trips(
    items: list[str],
    books: dict,
    gold_cost: dict[str, int],
    core_prices: dict[str, float] | None = None,
    ignore_gold: bool = True,
    cores: tuple[str, ...] = CORE_ORBS,
) -> tuple[list[TripResult], int, int]:
    """(ranked results, trips whose three books are scanned, all trips for `items`).

    Ranked by profit (in divine) when gold is ignored, else by profit per 1000 gold (trips with
    no known gold fee last). Profitable trips come before losing ones.
    """
    cp = {**DEFAULT_CORE_PRICES, **(core_prices or {})}
    results, evaluable, total = [], 0, 0
    for item in items:
        for a, x, b in round_trips(item, cores):
            total += 1
            if _trip_legs(a, x, b, books) is None:
                continue
            evaluable += 1
            r = evaluate_trip(a, x, b, books, gold_cost, cp)
            if r:
                results.append(r)

    def key(r: TripResult):
        metric = r.profit_div if ignore_gold else (
            r.profit_per_1000_gold if r.profit_per_1000_gold is not None else -math.inf
        )
        return (r.profit > 0, metric)

    results.sort(key=key, reverse=True)
    return results, evaluable, total
