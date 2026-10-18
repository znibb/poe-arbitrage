"""poe.ninja PoE 2 economy data, cached on disk with a fetch timestamp.

Remote data is only requested when the cached copy is older than `max_age_hours`
(default 12h). A refresh sends the stored ETag, so an unchanged response (304)
just renews the timestamp. If the network fails, stale cache is returned flagged
`stale=True`; with no cache at all the error is raised.

poe.ninja refreshes PoE 2 data about hourly and asks for modest request volume
and a descriptive User-Agent: https://poe.ninja/docs/api
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import project_path

BASE_URL = "https://poe.ninja/poe2/api/economy"
DEFAULT_MAX_AGE_HOURS = 12.0

# Display name -> poe.ninja `type` parameter.
CATEGORIES = {
    "Currency": "Currency",
    "Fragments": "Fragments",
    "Abyssal Bones": "Abyss",
    "Uncut Gems": "UncutGems",
    "Lineage Gems": "LineageSupportGems",
    "Essences": "Essences",
    "Soul Cores": "SoulCores",
    "Idols": "Idols",
    "Runes": "Runes",
    "Omens": "Ritual",
    "Expedition": "Expedition",
    "Liquid Emotions": "Delirium",
    "Catalysts": "Breach",
    "Verisium": "Verisium",
}
DEFAULT_CACHE_DIR = "data/ninja"
DEFAULT_USER_AGENT = "poearb-personal-research"

# (status, parsed JSON or None, etag or None); status 304 means "not modified".
HttpGet = Callable[[str, dict[str, str], str, str | None], tuple[int, Any, str | None]]


@dataclass
class Cached:
    data: Any
    fetched_at: float  # epoch seconds of the last successful fetch/revalidation
    from_cache: bool   # True if no remote request succeeded for this call
    stale: bool        # True if older than max age (remote refresh failed)


def _http_get(url: str, params: dict[str, str], user_agent: str, etag: str | None):
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    try:
        with urllib.request.urlopen(urllib.request.Request(full, headers=headers), timeout=30) as r:
            return r.status, json.load(r), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, etag
        raise


def _cache_path(cache_dir: str, key: str) -> Path:
    return Path(cache_dir) / (re.sub(r"[^\w.-]+", "_", key) + ".json")


def _read(path: Path) -> dict | None:
    try:
        entry = json.loads(path.read_text(encoding="utf8"))
        return entry if {"fetched_at", "data"} <= entry.keys() else None
    except (OSError, ValueError):
        return None


def _write(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    tmp.replace(path)


def _get_cached(
    key: str,
    url: str,
    params: dict[str, str],
    *,
    cache_dir: str,
    max_age_hours: float,
    user_agent: str,
    http_get: HttpGet,
    now: float | None,
) -> Cached:
    now = time.time() if now is None else now
    path = _cache_path(cache_dir, key)
    entry = _read(path)
    max_age = max_age_hours * 3600

    if entry and now - entry["fetched_at"] <= max_age:
        return Cached(entry["data"], entry["fetched_at"], from_cache=True, stale=False)

    try:
        status, data, etag = http_get(url, params, user_agent, entry.get("etag") if entry else None)
    except (OSError, ValueError):  # URLError/HTTPError are OSError; ValueError = bad JSON
        if entry:
            return Cached(entry["data"], entry["fetched_at"], from_cache=True, stale=True)
        raise

    if status == 304 and entry:
        data = entry["data"]
    _write(path, {"fetched_at": now, "etag": etag, "data": data})
    return Cached(data, now, from_cache=False, stale=False)


def get_leagues(
    *,
    cache_dir: str = DEFAULT_CACHE_DIR,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    user_agent: str = DEFAULT_USER_AGENT,
    http_get: HttpGet = _http_get,
    now: float | None = None,
) -> Cached:
    """League list; the first entry is the current challenge league."""
    return _get_cached(
        "leagues", f"{BASE_URL}/leagues", {},
        cache_dir=cache_dir, max_age_hours=max_age_hours,
        user_agent=user_agent, http_get=http_get, now=now,
    )


def get_overview(
    league: str,
    type_: str = "Currency",
    *,
    cache_dir: str = DEFAULT_CACHE_DIR,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    user_agent: str = DEFAULT_USER_AGENT,
    http_get: HttpGet = _http_get,
    now: float | None = None,
) -> Cached:
    """Exchange overview for one league and category (`type_`: Currency, Essences, ...)."""
    return _get_cached(
        f"overview_{league}_{type_}",
        f"{BASE_URL}/exchange/current/overview",
        {"league": league, "type": type_},
        cache_dir=cache_dir, max_age_hours=max_age_hours,
        user_agent=user_agent, http_get=http_get, now=now,
    )


def get_all_overviews(
    league: str,
    categories: dict[str, str] | None = None,
    *,
    delay: float = 1.0,
    **kwargs: Any,
) -> dict[str, Cached]:
    """Fetch/cache every category (display name -> Cached). Defaults to all of CATEGORIES.

    `delay` seconds are slept before each request that actually hits the network,
    to keep request volume modest. `kwargs` pass through to `get_overview`.
    """
    out: dict[str, Cached] = {}
    for label, type_ in (categories or CATEGORIES).items():
        res = get_overview(league, type_, **kwargs)
        out[label] = res
        if not res.from_cache and delay:
            time.sleep(delay)
    return out


def volume_by_name(overview: dict) -> dict[str, dict[str, Any]]:
    """Map item name -> {price, volume, max_volume_currency} from an overview payload.

    `price` and `volume` are in the overview's primary reference currency
    (`overview["core"]["primary"]`). Volume is per currency, not per pair.
    """
    # Top-level `items` names the lines; `core.items` names the reference currencies.
    names = {i["id"]: i["name"] for i in overview["core"]["items"]}
    names.update({i["id"]: i["name"] for i in overview.get("items", [])})
    return {
        names.get(line["id"], line["id"]): {
            "price": line["primaryValue"],
            "volume": line.get("volumePrimaryValue"),
            "max_volume_currency": line.get("maxVolumeCurrency"),
        }
        for line in overview["lines"]
    }


def top_by_volume(overviews: dict[str, Cached], n: int = 20) -> list[dict[str, Any]]:
    """The `n` highest-volume items across categories, highest first.

    Volume and price are in each overview's primary currency (divine for all
    categories seen so far), so they are comparable across categories.
    """
    rows = []
    for category, cached in overviews.items():
        primary = cached.data["core"]["primary"]
        for name, v in volume_by_name(cached.data).items():
            rows.append({"category": category, "name": name, "primary": primary,
                         "fetched_at": cached.fetched_at, "stale": cached.stale, **v})
    rows = [r for r in rows if r["volume"]]
    rows.sort(key=lambda r: r["volume"], reverse=True)
    return rows[:n]


def kwargs_from_config(cfg: dict) -> dict[str, Any]:
    """Cache/UA keyword arguments for the get_* functions from the `ninja:` config section."""
    n = cfg.get("ninja", {})
    return {
        "cache_dir": str(project_path(n.get("cache_dir", DEFAULT_CACHE_DIR))),
        "max_age_hours": n.get("max_age_hours", DEFAULT_MAX_AGE_HOURS),
        "user_agent": n.get("user_agent", DEFAULT_USER_AGENT),
    }
