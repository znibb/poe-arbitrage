"""Tesseract OCR of the exchange panel, anchored on the "MARKET RATIO" label.

The panel layout is fixed, so every field is cropped at an offset from the label's
position (scaled by the label's measured width, which covers UI-scale differences) and
read separately. That is far more reliable than OCRing the whole noisy panel.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from PIL import Image
from rapidfuzz import fuzz

# Native size of the "MARKET" word in pixels; the ratio to what we measure is the UI scale.
ANCHOR_WIDTH = 63.0
# The window title "CURRENCY EXCHANGE" is always visible (the Market Ratio header is hidden
# while the trades tables show), so it is the preferred anchor. Native measurements: the
# title's text spans ANCHOR_TITLE_WIDTH px; the "MARKET" word starts TITLE_DX px left of the
# title's right edge and TITLE_DY px below the top of the title's capital letters.
ANCHOR_TITLE_WIDTH = 254.0
TITLE_DX = 169.0
TITLE_DY = 45.0
# Crop boxes (x0, y0, x1, y1) in native pixels relative to the top-left of the "MARKET" word.
FIELDS = {
    "ratio": (-20, 22, 140, 46),
    "mid": (10, 70, 100, 94),
    "want_name": (-352, 52, -105, 110),
    "have_name": (195, 52, 445, 110),
    "gold": (38, 122, 110, 150),
    "pane_title": (-30, 1, 145, 27),  # heading of the first trades pane (title layout)
}
# Trades view: rows sit at a fixed pitch below the anchor; (first row centre, pitch, row count)
# for the Available and Competing tables, plus the horizontal extent of a row.
# Two layouts exist: below the Market Ratio header (anchored on "MARKET"), and, in the game,
# a pane that covers the header (anchored on the window title), which sits slightly differently.
TABLE_ROWS = {
    "market": {"available": (118.0, 25.2, 6), "competing": (334.0, 25.2, 6)},
    "title": {"available": (66.0, 25.2, 6), "competing": (282.0, 25.4, 6)},
}
ROW_X = {"market": (-52, 122), "title": (-45, 140)}
ROW_HALF_HEIGHT = 12
# A very long ratio ("1 : 3400.50") wraps: "1 :" stays on the row's line, the number drops to a
# second line. This taller window holds both without reaching the neighbouring rows' text.
WRAPPED_UPPER = (16, 0)  # spans (native px above, below the row centre) of the two lines
WRAPPED_LOWER = (-4, 19)
# Where the Ratio column ends and the Stock column begins (native px from the anchor); used when
# the two touch ("1 : 10000 10,000") and tesseract returns them as one run of digits.
ROW_SPLIT = {"market": 55, "title": 58}
THRESHOLDS = (105, 120, 130, 150)  # game text is light on dark; try a couple of cut-offs
NUMERIC = "-c tessedit_char_whitelist=0123456789:;.,<>"
OCR_SCALE = 3


@dataclass
class Anchor:
    left: int
    top: int
    scale: float
    layout: str = "market"  # "title" when found via the window title (header hidden)


def configure(tessdata_dir: str | None) -> None:
    if tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = tessdata_dir


def _upscale(gray: np.ndarray) -> np.ndarray:
    return cv2.resize(gray, None, fx=OCR_SCALE, fy=OCR_SCALE, interpolation=cv2.INTER_CUBIC)


def _binary(gray: np.ndarray, thr: int, pad: int = 0) -> np.ndarray:
    """Upscale and binarise to black text on white (light game text on a dark background)."""
    out = np.where(_upscale(gray) > thr, 0, 255).astype(np.uint8)
    return cv2.copyMakeBorder(out, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255) if pad else out


def _thresholds(gray: np.ndarray) -> list[int]:
    """Candidate cut-offs for a small crop: Otsu and variants, then fixed ones."""
    otsu, _ = cv2.threshold(_upscale(gray), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cands = [int(otsu), int(otsu * 0.8), int(otsu * 1.2), *THRESHOLDS]
    return list(dict.fromkeys(c for c in cands if 40 < c < 230))


def _gray(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)


def find_title_anchor(img: Image.Image) -> Anchor | None:
    """Locate the 'CURRENCY EXCHANGE' window title and derive the equivalent 'MARKET' anchor."""
    gray = _gray(img)
    top_rows = gray[: max(120, gray.shape[0] // 4)]
    for thr in (130, 150, 170):
        d = pytesseract.image_to_data(
            _binary(top_rows, thr), config="--psm 11", output_type=pytesseract.Output.DICT
        )
        cur = exc = None
        for i, t in enumerate(d["text"]):
            word = t.strip().upper()
            if fuzz.ratio(word, "CURRENCY") >= 75 and d["width"][i] > 60:
                cur = i
            elif fuzz.ratio(word, "EXCHANGE") >= 75 and d["width"][i] > 60:
                exc = i
        if cur is None or exc is None or d["left"][exc] <= d["left"][cur]:
            continue
        left = d["left"][cur] // OCR_SCALE
        right = (d["left"][exc] + d["width"][exc]) // OCR_SCALE
        scale = (right - left) / ANCHOR_TITLE_WIDTH
        if abs(scale - 1) < 0.08:  # the measured span is noisy by several px; snap the common case
            scale = 1.0
        # Text band: the rows (below the window frame line) where the letters are.
        cols = top_rows[:, max(0, left) : right] > 140
        counts = cols.sum(axis=1)
        need = 0.15 * (right - left)
        rows = [y for y in range(6, len(counts)) if counts[y] >= need]
        if not rows:
            continue
        band_top = rows[0]
        return Anchor(
            round(right - TITLE_DX * scale), round(band_top + TITLE_DY * scale), scale, "title"
        )
    return None


def find_anchor(img: Image.Image) -> Anchor | None:
    """Locate the exchange panel: by its window title, else by the 'MARKET' word of the
    Market Ratio header. Returns the position of the 'MARKET' word and the measured UI scale."""
    return find_title_anchor(img) or find_market_anchor(img)


def find_market_anchor(img: Image.Image) -> Anchor | None:
    """Locate the 'MARKET' word of the Market Ratio header, with the measured UI scale."""
    gray = _gray(img)
    for thr in (150, 130, 170):
        d = pytesseract.image_to_data(
            _binary(gray, thr), config="--psm 11", output_type=pytesseract.Output.DICT
        )
        for i, t in enumerate(d["text"]):
            if fuzz.ratio(t.strip().upper(), "MARKET") >= 80 and d["width"][i] > 20:
                w = d["width"][i] / OCR_SCALE
                return Anchor(d["left"][i] // OCR_SCALE, d["top"][i] // OCR_SCALE, w / ANCHOR_WIDTH)
    return None


def crop_field(img: Image.Image, anchor: Anchor, name: str) -> Image.Image:
    x0, y0, x1, y1 = FIELDS[name]
    s = anchor.scale
    box = (
        max(0, round(anchor.left + x0 * s)),
        max(0, round(anchor.top + y0 * s)),
        min(img.width, round(anchor.left + x1 * s)),
        min(img.height, round(anchor.top + y1 * s)),
    )
    return img.crop(box)


def crop_row(
    img: Image.Image,
    anchor: Anchor,
    centre: float,
    part: str | None = None,
    span: tuple[float, float] = (ROW_HALF_HEIGHT, ROW_HALF_HEIGHT),
) -> Image.Image:
    """Crop one trades-table row (or only its "ratio" / "stock" column);
    `centre` is in native pixels below the anchor top."""
    s = anchor.scale
    x0, x1 = ROW_X[anchor.layout]
    if part == "ratio":
        x1 = ROW_SPLIT[anchor.layout]
    elif part == "stock":
        x0 = ROW_SPLIT[anchor.layout]
    left = max(0, round(anchor.left + x0 * s))
    right = min(img.width, round(anchor.left + x1 * s))
    top = min(img.height, max(0, round(anchor.top + (centre - span[0]) * s)))
    bottom = min(img.height, round(anchor.top + (centre + span[1]) * s))
    return img.crop((left, top, max(left, right), max(top, bottom)))


def read_text(
    img: Image.Image,
    *,
    psm: int = 7,
    numeric: bool = False,
    validate: Callable[[str], bool] | None = None,
    early: int = 0,
) -> tuple[str, float]:
    """OCR a small crop; returns (text, mean word confidence 0-100).

    Tries several binarisation thresholds and takes a vote: texts passing `validate` win,
    then the text most thresholds agree on, then the highest confidence. (Tesseract reports
    confidence 0 for some correct short numbers, so confidence alone is a poor judge.)
    """
    gray = _gray(img)
    cfg = f"--psm {psm} " + (NUMERIC if numeric else "")
    votes: dict[str, list[float]] = {}
    for thr in _thresholds(gray):
        d = pytesseract.image_to_data(
            _binary(gray, thr, pad=10), config=cfg, output_type=pytesseract.Output.DICT
        )
        words = [(t, float(c)) for t, c in zip(d["text"], d["conf"]) if t.strip() and float(c) >= 0]
        if words:
            text = " ".join(t for t, _ in words)
            votes.setdefault(text, []).append(sum(c for _, c in words) / len(words))
            # Only a text with a decimal point may stop early: tesseract often drops a faint
            # point ("7.14" -> "714"), and a later threshold may still see it.
            if early and re.search(r"\d\.\d", text) and validate and validate(text) and len(votes[text]) >= early:
                break
    return choose(votes, validate)


def _canon(text: str) -> str:
    """Comparison key of a reading: no whitespace, no stray leading/trailing punctuation, and no
    decimal points (tesseract drops faint ones, so "7.14" and "714" are the same reading)."""
    return re.sub(r"\s+", "", text).strip(".,;").replace(".", "")


def choose(votes: dict[str, list[float]], validate: Callable[[str], bool] | None = None) -> tuple[str, float]:
    """Pick the reading from {text: confidences per agreeing threshold}.

    Readings that differ only in spacing, stray punctuation at the edges or decimal points count
    as one. Within such a group the text passing `validate` is used, preferring the one with a
    decimal point when at least two thresholds saw it: tesseract often drops a faint point
    ("7.14" read as "714"); a point seen only once is more likely an invented one.
    Groups are ranked: valid first, then the most thresholds in agreement, then the highest
    confidence. (Tesseract reports confidence 0 for some correct short numbers, so confidence
    alone is a poor judge.)
    """
    if not votes:
        return "", 0.0
    groups: dict[str, list[tuple[str, list[float]]]] = {}
    for text, confs in votes.items():
        groups.setdefault(_canon(text), []).append((text, confs))
    best_key = None
    best: tuple[str, float] = ("", 0.0)
    for variants in groups.values():
        ok = [v for v in variants if not validate or validate(v[0])]
        pool = ok or variants
        dotted = [v for v in pool if re.search(r"\d\.\d", v[0])]  # a decimal point, not stray dots
        if sum(len(v[1]) for v in dotted) < 2:
            dotted = []  # a point seen once may be an invented one ("1843" read as "18.43")
        text = max(dotted or pool, key=lambda v: (len(v[1]), -len(v[0])))[0]  # ties: the cleaner text
        confs = [c for _, cs in variants for c in cs]
        key = (bool(validate and ok), len(confs), max(confs))
        if best_key is None or key > best_key:
            best_key, best = key, (text, max(confs))
    return best
