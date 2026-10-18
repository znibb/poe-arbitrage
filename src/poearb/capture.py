"""Screen capture. Read-only: grabs pixels, never interacts with the game."""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def grab_fullscreen() -> Image.Image:
    """Full-screen capture via KDE's Spectacle (works on Wayland, no GUI shown)."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "shot.png"
        subprocess.run(
            ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", str(out)],
            check=True,
            timeout=15,
        )
        return Image.open(out).convert("RGB")


def grab_region(region: dict[str, int]) -> Image.Image:
    img = grab_fullscreen()
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    return img.crop((x, y, x + w, y + h))
