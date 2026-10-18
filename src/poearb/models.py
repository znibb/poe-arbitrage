from dataclasses import dataclass, field


@dataclass(frozen=True)
class Offer:
    """One scanned exchange view: what you give for what you want."""

    give_cur: str
    give_qty: int
    want_cur: str
    want_qty: int
    gold_fee: int | None
    ocr_conf: float

    @property
    def rate(self) -> float:
        """Units of `want_cur` obtained per unit of `give_cur`."""
        return self.want_qty / self.give_qty


@dataclass(frozen=True)
class TradeRow:
    """One row of the exchange's Available/Competing Trades tables."""

    ratio: tuple[float, float]  # shown as "want : have"
    stock: int
    aggregate: str | None = None  # "<" / ">" on the summarised last row of a table


@dataclass
class ScanResult:
    """Everything read from one capture of the exchange panel."""

    want_cur: str | None = None
    have_cur: str | None = None
    market_ratio: tuple[float, float] | None = None  # "want : have"
    want_qty: int | None = None  # effective quantities from the middle "a : b" field
    have_qty: int | None = None
    gold_fee: int | None = None  # as read from the panel (hidden in the trades view)
    gold_per_item: int | None = None  # looked up in gold_cost.json for want_cur
    available: list[TradeRow] = field(default_factory=list)
    competing: list[TradeRow] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def no_offers(self) -> bool:
        """Trades view shown, but nobody is offering the wanted currency (Available pane empty)."""
        return not self.available and bool(self.competing)
