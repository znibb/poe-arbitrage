import json
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ("config.local.yaml", "config.yaml")


def project_path(p: str | Path) -> Path:
    """Resolve a config path: absolute stays, relative is anchored at the project root."""
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_gold_cost(path: str = "gold_cost.json") -> dict[str, int]:
    """Per-item gold cost by item name (see gold_cost.json)."""
    return json.loads(project_path(path).read_text(encoding="utf8"))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config.yaml, overlaid with config.local.yaml if present."""
    if path:
        return yaml.safe_load(Path(path).read_text())
    cfg: dict[str, Any] = {}
    for name in reversed(DEFAULT_PATHS):
        p = project_path(name)
        if p.exists():
            cfg = _merge(cfg, yaml.safe_load(p.read_text()) or {})
    return cfg


def save_setting(section: str, key: str, value: Any, path: str = "config.local.yaml") -> None:
    """Persist one setting in the local override file (config.yaml itself is never rewritten)."""
    p = project_path(path)
    cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
    cfg = _merge(cfg or {}, {section: {key: value}})
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))


def save_region(region: dict[str, int], path: str = "config.local.yaml") -> None:
    save_setting("capture", "region", region, path)
