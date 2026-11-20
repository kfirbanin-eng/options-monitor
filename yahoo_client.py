"""Read-only Yahoo Finance chart client (free, no account, no keys).

Used for the underlying and the market panel when you don't have broker API
keys. Same safety idea as schwab_client.py: every request is checked before it
is sent - GET only, only the Yahoo chart endpoint, no redirects.

Note: this is Yahoo's public website endpoint, not an official API. It can be
rate-limited or change format; the monitor keeps running and shows the error.
"""
from __future__ import annotations

import time
from urllib.parse import quote, unquote, urlsplit

import requests

ALLOWED_HOSTS = {"query1.finance.yahoo.com", "query2.finance.yahoo.com"}
CHART_PREFIX = "/v8/finance/chart/"


class YahooViolation(RuntimeError):
    pass


def check_allowed(method: str, url: str) -> None:
    parts = urlsplit(url)
    if (method or "").upper() != "GET":
        raise YahooViolation(f"Blocked {method}: Yahoo client is GET-only")
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443) \
            or parts.username or parts.password:
        raise YahooViolation(f"Blocked request to {parts.hostname}")
    path = unquote(parts.path)
    if not path.startswith(CHART_PREFIX) or ".." in path or "//" in path[1:]:
        raise YahooViolation(f"Blocked path {parts.path}")


class _Guarded(requests.Session):
    def request(self, method, url, *args, **kwargs):
        check_allowed(method, url)
        kwargs["allow_redirects"] = False
        return super().request(method, url, *args, **kwargs)

    def send(self, request, **kwargs):
        check_allowed(request.method, request.url)
        return super().send(request, **kwargs)


class YahooClient:
    BASE = "https://query1.finance.yahoo.com"

    def __init__(self, cache_sec: float = 8.0):
        self.http = _Guarded()
        self.http.headers.update({"User-Agent": "Mozilla/5.0 (options-monitor; read-only)",
                                  "Accept": "application/json"})
        self.cache_sec = cache_sec
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def chart(self, symbol: str, interval: str, rng: str) -> dict:
        key = (symbol, interval, rng)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_sec:
            return hit[1]
        url = f"{self.BASE}{CHART_PREFIX}{quote(symbol, safe='')}"
        params = {"interval": interval, "range": rng, "includePrePost": "false"}
        for attempt in range(3):
            try:
                r = self.http.get(url, params=params, timeout=15)
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            err = (data.get("chart") or {}).get("error")
            if err:
                raise RuntimeError(f"Yahoo error for {symbol}: {err}")
            result = data["chart"]["result"][0]
            self._cache[key] = (time.time(), result)
            return result
        raise RuntimeError(f"Yahoo request for {symbol} failed")
