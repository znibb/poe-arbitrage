import argparse
import sys

from .capture import grab_fullscreen, grab_region
from . import ninja
from .config import load_config, save_region


def cmd_calibrate(args, cfg) -> int:
    parts = args.region.split(",")
    if len(parts) != 4:
        print("--region must be x,y,w,h", file=sys.stderr)
        return 2
    x, y, w, h = map(int, parts)
    save_region({"x": x, "y": y, "w": w, "h": h})
    print("Saved region to config.local.yaml")
    return 0


def cmd_capture(args, cfg) -> int:
    """Save a capture to disk (full screen with --full, otherwise the configured region)."""
    region = cfg["capture"].get("region")
    if not args.full and not region:
        print("no capture region set (use the GUI's 'Calibrate capture' or --full)", file=sys.stderr)
        return 2
    img = grab_fullscreen() if args.full else grab_region(region)
    img.save(args.out)
    print(f"Wrote {args.out} ({img.width}x{img.height})")
    return 0


def cmd_gui(args, cfg) -> int:
    try:
        from .gui.app import run
    except ImportError as e:
        print(f"GUI needs PySide6 ({e}); install the 'gui' extra or the system pyside6 package", file=sys.stderr)
        return 1
    return run(cfg)


def cmd_trigger(args, cfg) -> int:
    """Send SIGUSR1 to the running GUI (bind this to a global shortcut)."""
    from . import signals

    if signals.send_trigger():
        return 0
    print("poearb is not running", file=sys.stderr)
    return 1


def cmd_scan(args, cfg) -> int:
    """Capture the calibrated region and scan it once, without the GUI (prints the result)."""
    from . import ocr
    from .scan import capture_and_scan

    ocr.configure(cfg.get("ocr", {}).get("tessdata_dir"))
    res, path = capture_and_scan(cfg)
    print(f"capture: {path}")
    print(res)
    return 1 if res.warnings else 0


def cmd_analyze(args, cfg) -> int:
    """Refresh poe.ninja data if stale and list the highest-volume items."""
    from datetime import datetime

    from rich.console import Console
    from rich.table import Table

    ncfg = cfg.get("ninja", {})
    kw = ninja.kwargs_from_config(cfg)
    console = Console()
    league = ncfg.get("league") or ninja.get_leagues(**kw).data[0]["id"]
    overviews = ninja.get_all_overviews(league, **kw)
    top = ninja.top_by_volume(overviews, args.top or ncfg.get("top_n", 20))

    oldest = min(o.fetched_at for o in overviews.values())
    console.print(
        f"League: {league}  |  poe.ninja data from {datetime.fromtimestamp(oldest):%Y-%m-%d %H:%M}"
        + ("  [yellow](STALE: refresh failed)[/yellow]" if any(o.stale for o in overviews.values()) else "")
    )
    table = Table(title=f"Top {len(top)} by traded volume (in {top[0]['primary'] if top else '?'})")
    for col in ("#", "Item", "Category", "Volume", "Price", "Busiest pair"):
        table.add_column(col, justify="right" if col in ("#", "Volume", "Price") else "left")
    for i, r in enumerate(top, 1):
        table.add_row(str(i), r["name"], r["category"], f"{r['volume']:,.0f}",
                      f"{r['price']:.4g}", r["max_volume_currency"] or "")
    console.print(table)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="poearb")
    p.add_argument("--config")
    p.set_defaults(fn=cmd_gui)  # no subcommand: open the window
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("calibrate", help="save the exchange panel region")
    c.add_argument("--region", required=True, help="x,y,w,h in screen pixels")
    c.set_defaults(fn=cmd_calibrate)

    c = sub.add_parser("capture", help="save a screenshot (for building fixtures)")
    c.add_argument("--out", default="capture.png")
    c.add_argument("--full", action="store_true")
    c.set_defaults(fn=cmd_capture)

    sub.add_parser("gui", help="open the window (default)").set_defaults(fn=cmd_gui)
    sub.add_parser("trigger", help="tell the running GUI to scan (sends SIGUSR1)").set_defaults(fn=cmd_trigger)
    sub.add_parser("scan", help="capture and OCR once without the GUI").set_defaults(fn=cmd_scan)
    c = sub.add_parser("analyze", help="top poe.ninja items by volume (refreshes stale data)")
    c.add_argument("--top", type=int, help="number of items (default: ninja.top_n or 20)")
    c.set_defaults(fn=cmd_analyze)

    args = p.parse_args()
    return args.fn(args, load_config(args.config))


if __name__ == "__main__":
    sys.exit(main())
