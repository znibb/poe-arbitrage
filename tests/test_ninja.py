import urllib.error

import pytest

from poearb import ninja

H = 3600


class FakeHttp:
    def __init__(self, payload="DATA", etag="e1"):
        self.calls = []
        self.payload, self.etag = payload, etag
        self.fail = False
        self.not_modified = False

    def __call__(self, url, params, ua, etag):
        self.calls.append((url, params, ua, etag))
        if self.fail:
            raise urllib.error.URLError("down")
        if self.not_modified and etag:
            return 304, None, etag
        return 200, self.payload, self.etag


def fetch(http, now, cache_dir):
    return ninja.get_overview("L", "Currency", cache_dir=str(cache_dir), http_get=http, now=now)


def test_fresh_cache_skips_remote(tmp_path):
    http = FakeHttp()
    first = fetch(http, 1000, tmp_path)
    second = fetch(http, 1000 + 11 * H, tmp_path)
    assert len(http.calls) == 1
    assert not first.from_cache and second.from_cache
    assert second.data == "DATA" and second.fetched_at == 1000


def test_old_cache_refetches_with_etag(tmp_path):
    http = FakeHttp()
    fetch(http, 1000, tmp_path)
    http.payload = "NEW"
    http.etag = "e2"
    res = fetch(http, 1000 + 13 * H, tmp_path)
    assert len(http.calls) == 2 and http.calls[1][3] == "e1"
    assert res.data == "NEW" and res.fetched_at == 1000 + 13 * H


def test_304_keeps_data_and_renews_timestamp(tmp_path):
    http = FakeHttp()
    fetch(http, 1000, tmp_path)
    http.not_modified = True
    res = fetch(http, 1000 + 13 * H, tmp_path)
    assert res.data == "DATA" and res.fetched_at == 1000 + 13 * H and not res.stale
    assert fetch(http, 1000 + 14 * H, tmp_path).from_cache


def test_network_failure_returns_stale_cache(tmp_path):
    http = FakeHttp()
    fetch(http, 1000, tmp_path)
    http.fail = True
    res = fetch(http, 1000 + 13 * H, tmp_path)
    assert res.stale and res.from_cache and res.data == "DATA" and res.fetched_at == 1000


def test_network_failure_without_cache_raises(tmp_path):
    http = FakeHttp()
    http.fail = True
    with pytest.raises(urllib.error.URLError):
        fetch(http, 1000, tmp_path)


def test_volume_by_name():
    overview = {
        "core": {"items": [{"id": "exalted", "name": "Exalted Orb"}]},
        "lines": [{"id": "exalted", "primaryValue": 0.00134, "volumePrimaryValue": 1143,
                   "maxVolumeCurrency": "divine"}],
    }
    assert ninja.volume_by_name(overview) == {
        "Exalted Orb": {"price": 0.00134, "volume": 1143, "max_volume_currency": "divine"}
    }
