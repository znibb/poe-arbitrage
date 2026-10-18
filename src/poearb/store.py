import sqlite3
import time
from pathlib import Path
from statistics import median

from .models import Offer, ScanResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    give_cur TEXT NOT NULL, give_qty INTEGER NOT NULL,
    want_cur TEXT NOT NULL, want_qty INTEGER NOT NULL,
    gold_fee INTEGER,
    ocr_conf REAL
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    want_cur TEXT NOT NULL, have_cur TEXT NOT NULL,
    market_want REAL NOT NULL, market_have REAL NOT NULL,
    want_qty INTEGER, have_qty INTEGER, gold_fee INTEGER,
    confidence REAL, capture_path TEXT
);
CREATE TABLE IF NOT EXISTS trade_rows (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    kind TEXT NOT NULL CHECK (kind IN ('available', 'competing')),
    pos INTEGER NOT NULL,
    ratio_want REAL NOT NULL, ratio_have REAL NOT NULL,
    stock INTEGER NOT NULL, aggregate TEXT
);
CREATE INDEX IF NOT EXISTS offers_pair ON offers (give_cur, want_cur, ts);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    return con


def add_offer(con: sqlite3.Connection, o: Offer) -> None:
    con.execute(
        "INSERT INTO offers (ts, give_cur, give_qty, want_cur, want_qty, gold_fee, ocr_conf)"
        " VALUES (?,?,?,?,?,?,?)",
        (time.time(), o.give_cur, o.give_qty, o.want_cur, o.want_qty, o.gold_fee, o.ocr_conf),
    )
    con.commit()


def add_scan(con: sqlite3.Connection, r: ScanResult, capture_path: str | None = None) -> int:
    """Store a validated scan (and its trade rows); also feeds the gold-fee learner."""
    cur = con.execute(
        "INSERT INTO scans (ts, want_cur, have_cur, market_want, market_have, want_qty, have_qty,"
        " gold_fee, confidence, capture_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (time.time(), r.want_cur, r.have_cur, *r.market_ratio, r.want_qty, r.have_qty,
         r.gold_fee, r.confidence, capture_path),
    )
    scan_id = cur.lastrowid
    for kind, rows in (("available", r.available), ("competing", r.competing)):
        con.executemany(
            "INSERT INTO trade_rows VALUES (?,?,?,?,?,?,?)",
            [(scan_id, kind, i, t.ratio[0], t.ratio[1], t.stock, t.aggregate) for i, t in enumerate(rows)],
        )
    con.commit()
    if r.gold_fee is not None and r.want_qty and r.have_qty:
        add_offer(con, Offer(r.have_cur, r.have_qty, r.want_cur, r.want_qty, r.gold_fee, r.confidence))
    return scan_id


def gold_cost_estimates(con: sqlite3.Connection, confirm_n: int = 3) -> dict[str, dict]:
    """Per-item gold cost per currency, derived from observations.

    The fee scales with the "I WANT" quantity, so per_item = gold_fee / want_qty,
    keyed by want_cur. Confirmed when >= confirm_n observations agree exactly.
    """
    rows = con.execute(
        "SELECT want_cur, gold_fee, want_qty FROM offers WHERE gold_fee IS NOT NULL"
    ).fetchall()
    obs: dict[str, list[float]] = {}
    for cur, fee, qty in rows:
        obs.setdefault(cur, []).append(fee / qty)
    return {
        cur: {
            "per_item_gold": median(v),
            "n_obs": len(v),
            "spread": max(v) - min(v),
            "confirmed": len(v) >= confirm_n and max(v) == min(v),
        }
        for cur, v in obs.items()
    }
