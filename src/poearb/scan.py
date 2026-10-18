"""Orchestrates one scan: screenshot of the exchange panel -> ScanResult."""

from PIL import Image
from rapidfuzz import fuzz

from . import ocr, parse
from .models import ScanResult, TradeRow


def scan_image(
    img: Image.Image,
    names: list[str],
    gold_cost: dict[str, int],
    min_confidence: float = 60.0,
) -> ScanResult:
    res = ScanResult()
    anchor = ocr.find_anchor(img)
    if anchor is None:
        res.warnings.append("exchange window not found (no 'CURRENCY EXCHANGE' title or 'Market Ratio' header): is it open in the captured region?")
        return res

    confs: list[float] = []

    def read(field: str, **kw) -> str:
        # Short numbers often get confidence 0 from tesseract despite being right, so only
        # text fields (names) and table rows count towards the overall confidence.
        text, conf = ocr.read_text(ocr.crop_field(img, anchor, field), **kw)
        if text and not kw.get("numeric"):
            confs.append(conf)
        return text

    def one_sided(text: str) -> bool:
        r = parse.parse_ratio(text)  # the game always shows one side as 1
        return r is not None and 1.0 in r

    for side, field in (("want", "want_name"), ("have", "have_name")):
        text = read(field, psm=6)
        name, score = parse.match_name(text, names)
        setattr(res, f"{side}_cur", name)
        if name is None:
            res.warnings.append(f"could not match {side} currency from '{text}'")

    _read_tables(img, anchor, res)
    tables_shown = bool(res.available or res.competing)
    if tables_shown:  # the header is hidden by (or sits above) the tables: best offer = market ratio
        res.market_ratio = res.available[0].ratio if res.available else None
    else:
        res.market_ratio = parse.parse_ratio(read("ratio", numeric=True, validate=one_sided))
        # (with the trades tables showing, quantities and fee are hidden)
        mid = parse.parse_ratio(read("mid", numeric=True, validate=one_sided))
        if mid:
            res.want_qty, res.have_qty = int(mid[0]), int(mid[1])
        # The fee is a known multiple of the wanted item's cost: a reading that matches it is
        # taken over look-alike misreads (6 vs 0); if none does, the mismatch is warned about below.
        want_cost = gold_cost.get(res.want_cur or "")
        expected = want_cost * res.want_qty if want_cost and res.want_qty else None
        res.gold_fee = parse.parse_int(
            read("gold", numeric=True, validate=lambda t: expected is not None and parse.parse_int(t) == expected)
        )

    # Without available trades there is no market ratio; that is normal (see ScanResult.no_offers).
    if not tables_shown and res.market_ratio is None:
        res.warnings.append("market ratio not read")
    if not tables_shown and res.gold_fee is None:
        res.warnings.append("gold fee not read")

    res.confidence = sum(confs) / len(confs) if confs else 0.0
    if res.confidence < min_confidence:
        res.warnings.append(f"low OCR confidence ({res.confidence:.0f} < {min_confidence:.0f})")
    res.gold_per_item = gold_cost.get(res.want_cur) if res.want_cur else None
    if res.want_cur and res.gold_per_item is None:
        res.warnings.append(f"no gold cost known for {res.want_cur}")
    if w := parse.check_gold_fee(res.gold_fee, res.want_cur, res.want_qty, gold_cost):
        res.warnings.append(w)
    return res


def _read_tables(img: Image.Image, anchor: ocr.Anchor, res: ScanResult) -> None:
    """Fill the trades tables if showing.

    Rows sit at a fixed pitch below the anchor, so each is cropped and read on its own
    (reading the whole table in one go drops or garbles rows). A table can hold fewer than
    6 rows (the rest are blank, and there is then no '<' / '>' summary row), so trailing blank
    slots are normal; only a blank slot *between* rows is reported as a misread. Fewer than 2
    parsed rows in total means the basic view is showing and nothing is filled.
    """
    def valid(text: str) -> bool:
        return parse.parse_trade_row(text) is not None

    layout = ocr.TABLE_ROWS[anchor.layout]
    kinds = list(layout)
    if anchor.layout == "title" and _first_pane_is_competing(img, anchor):
        # With no available trades the Competing pane moves up into the first pane's place.
        layout = {"competing": layout["available"]}
    tables: dict[str, list] = {"available": [], "competing": []}
    for kind, (first, pitch, n) in layout.items():
        slots = []
        for i in range(n):
            row_img = ocr.crop_row(img, anchor, first + i * pitch)
            if row_img.height < 8 or row_img.width < 8:  # region ends above the table
                break
            text, _ = ocr.read_text(row_img, numeric=True, validate=valid, early=2)
            row = parse.parse_trade_row(text)
            centre = first + i * pitch
            if row is None and text.strip():
                # Wide ratio and stock can touch ("1 : 10000 10,000"): read the columns apart.
                row = _read_row_columns(img, anchor, centre)
            if row is None and text.strip():
                # A very long ratio wraps onto a second line ("1 :" / "3400.50").
                row = _read_row_columns(img, anchor, centre, wrapped=True)
            slots.append(row)
        while slots and slots[-1] is None:
            slots.pop()
        if any(r is None for r in slots):
            res.warnings.append(f"a {kind} trade row could not be read")
        tables[kind] = [r for r in slots if r]
    if len(tables["available"]) + len(tables["competing"]) < 2 and kinds:
        res.warnings.clear()
        return
    res.available = parse.repair_table(tables["available"])
    res.competing = parse.repair_table(tables["competing"])


def _read_row_columns(
    img: Image.Image, anchor: ocr.Anchor, centre: float, wrapped: bool = False
) -> TradeRow | None:
    """Read a table row's Ratio and Stock columns separately.

    With `wrapped`, the ratio is read as two lines ("1 :" on the row, the long number below it).
    """
    def num(span=(ocr.ROW_HALF_HEIGHT, ocr.ROW_HALF_HEIGHT), part="ratio") -> str:
        crop = ocr.crop_row(img, anchor, centre, part, span)
        if crop.height < 4 or crop.width < 4:  # the region ends above this part of the table
            return ""
        return ocr.read_text(crop, numeric=True, early=2)[0]

    if wrapped:
        upper = num(ocr.WRAPPED_UPPER)
        if ":" not in upper:  # small and faint; table rows always start "1 :", as in the lower line
            upper = "1 :"
        ratio = f"{upper} {num(ocr.WRAPPED_LOWER)}"
    else:
        ratio = num()
    return parse.parse_trade_row(f"{ratio} {num(part='stock')}")


def _first_pane_is_competing(img: Image.Image, anchor: ocr.Anchor) -> bool:
    """True if the topmost trades pane is headed 'Competing Trades' (no Available pane shown)."""
    text, _ = ocr.read_text(ocr.crop_field(img, anchor, "pane_title"), validate=None)
    word = text.upper()
    return fuzz.partial_ratio(word, "COMPETING") > max(70, fuzz.partial_ratio(word, "AVAILABLE"))


def capture_and_scan(cfg: dict) -> tuple[ScanResult, str | None]:
    """Screenshot the calibrated region, OCR it, and store it if it validated cleanly.

    Returns (result, saved capture path). Raises if no region is calibrated or capture fails.
    """
    import time

    from . import capture, store
    from .config import load_gold_cost, project_path

    region = cfg.get("capture", {}).get("region")
    if not region:
        raise RuntimeError("no capture region set: use 'Calibrate capture' first")
    ocr.configure(cfg.get("ocr", {}).get("tessdata_dir"))

    img = capture.grab_region(region)
    cap_dir = project_path(cfg.get("scan", {}).get("capture_dir", "data/captures"))
    cap_dir.mkdir(parents=True, exist_ok=True)
    path = cap_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.png"
    img.save(path)
    for old in sorted(cap_dir.glob("*.png"))[: -int(cfg.get("scan", {}).get("keep_captures", 50))]:
        old.unlink(missing_ok=True)

    gold_cost = load_gold_cost()
    names = list(gold_cost)
    res = scan_image(img, names, gold_cost, cfg.get("ocr", {}).get("min_confidence", 70))

    if res.want_cur and res.have_cur and res.market_ratio and not res.warnings:
        con = store.connect(str(project_path(cfg.get("storage", {}).get("db_path", "data/poearb.sqlite"))))
        try:
            store.add_scan(con, res, str(path))
        finally:
            con.close()
    return res, str(path)
