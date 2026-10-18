# poe-arbitrage

Read-only helper for the Path of Exile 2 currency exchange. It reads the screen (never sends
input to the game, never touches its memory or network traffic), OCRs the exchange panel, and
shows poe.ninja trade volumes. Target: Arch/Artix, KDE Plasma on Wayland.

## Setup

```sh
sudo pacman -S tesseract tesseract-data-eng spectacle pyside6
python -m venv --system-site-packages .venv && . .venv/bin/activate   # sees the system PySide6
pip install -e '.[dev]'
pytest
```

(The OCR tests are tuned against the system `tesseract-data-eng` model, which is what the app uses; a
different tessdata build (e.g. `tessdata_fast`) reads some digits differently.)

(`pip install -e '.[gui]'` installs PySide6 into the venv instead of using the system package.
Without `tesseract-data-eng`, OCR tests are skipped and scans fail; set `ocr.tessdata_dir` if
tesseract can't find its data.)

## Usage

`poearb` opens the window:

- **Check trade volume**: refreshes poe.ninja data if the cache is older than 12h and lists the
  top N items by traded volume. N is the text field (whole number, 1-500). The data timestamp is
  shown at the bottom.
- **Triangle scan order**: after each volume check, lists the scans to run for round trips through
  the core orbs (A -> item -> B -> A, A and B among Exalted/Chaos/Divine). Each exchange screen shows
  both sides of a pair (available = asks, competing = bids), so every pair is scanned once: the three
  core pairs first, then each top-volume item against each core (3 + 3 per item). Scans you take
  are ticked off with the best ratio seen; the next one is marked. **Ignore Exalted Orb** drops the
  Exalted Orb from the core pool (late in a league it is nearly worthless): only Chaos and Divine
  remain, giving 1 core pair and 2 scans per item.
- **Round-trip profit**: from the scanned books it computes what each round trip A -> item -> B -> A
  would earn. A screen "I WANT W, I HAVE H" is read as: Available Trades = asks (stock in W,
  you buy W with H), Competing Trades = bids (stock in H, you sell W for H). The three books of a
  trip are walked level by level with whole-unit quantities; the start amount with the highest
  profit is shown (profit in the starting orb and in divine, margin, gold fees, age of the oldest
  scan). **Ignore gold cost** (default on): rank by profit; off: rank by profit per 1000 gold.
  The checkbox states and Top N are kept in `config.local.yaml`. Assumption: the `<`/`>` summary
  rows count as one more level at their ratio with their stock.
- **Calibrate capture**: freezes the screen and lets you move/resize a rectangle over the exchange
  panel. Enter saves it (to `config.local.yaml`), Esc cancels, Tab moves to the next monitor.
  Include the "CURRENCY EXCHANGE" window title (the scan anchors on it, because the Market Ratio
  header is hidden while the trades tables show) and everything down to the bottom of the
  Competing Trades table (about 485 px below the Market Ratio header).
- **Scan** (SIGUSR1): captures the calibrated region, OCRs it and shows market ratio, currencies,
  quantities, gold fee and both trade tables. Clean scans are stored in `data/poearb.sqlite`;
  scans with warnings (low confidence, gold fee not matching `gold_cost.json`) are shown but not stored.

Bind a global shortcut (System Settings > Shortcuts > Add Command) to:

```sh
/path/to/poe-arbitrage/.venv/bin/poearb trigger     # same as: kill -USR1 $(cat $XDG_RUNTIME_DIR/poearb.pid)
```

Other commands: `poearb scan` (one scan without the window), `poearb analyze` (volume table in the
terminal), `poearb capture --full --out tests/fixtures/x.png` (make fixtures).

## Gold fees

The fee scales with the "I WANT" quantity. `gold_cost.json` holds the known per-item costs and is
used to sanity-check OCR; observed fees are also stored so `store.gold_cost_estimates` can
confirm or contradict them.
