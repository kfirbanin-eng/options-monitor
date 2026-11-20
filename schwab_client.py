"""Read-only Schwab Trader API client (market data only).

READ-ONLY GUARANTEE
-------------------
Every HTTP request goes through `GuardedSession.request`, which allows exactly:
  * GET  https://api.schwabapi.com/marketdata/v1/...   (quotes, price history)
  * POST https://api.schwabapi.com/v1/oauth/token      (OAuth token refresh)
Anything else - in particular anything under /trader/v1/ (accounts, orders) -
raises ReadOnlyViolation before a single byte is sent. This client has no
order methods at all; the guard is a second line of defence.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import requests

API_HOST = "api.schwabapi.com"
BASE = f"https://{API_HOST}"
MARKETDATA_PREFIX = "/marketdata/v1/"
TOKEN_URL = f"{BASE}/v1/oauth/token"
AUTHORIZE_URL = f"{BASE}/v1/oauth/authorize"


class ReadOnlyViolation(RuntimeError):
    pass


class GuardedSession(requests.Session):
    def request(self, method, url, *args, **kwargs):
        check_allowed(method, url)
        # Redirects are followed inside send(), not request(), so they would skip the guard.
        kwargs["allow_redirects"] = False
        return super().request(method, url, *args, **kwargs)

    def send(self, request, **kwargs):
        check_allowed(request.method, request.url)   # check the final, normalised URL too
        return super().send(request, **kwargs)


def check_allowed(method: str, url: str) -> None:
    method = (method or "").upper()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != API_HOST or parts.port not in (None, 443) \
            or parts.username or parts.password:
        raise ReadOnlyViolation(f"Blocked {method} {parts.scheme}://{parts.hostname}: only {API_HOST} is allowed")
    decoded = unquote(parts.path)
    if ".." in decoded.split("/") or "\\" in decoded or "//" in decoded or decoded != parts.path.replace("%20", " "):
        raise ReadOnlyViolation(f"Blocked {method}: path traversal / encoded path not allowed")
    if method == "GET" and parts.path.startswith(MARKETDATA_PREFIX):
        return
    if method == "POST" and f"{parts.scheme}://{parts.hostname}{parts.path}" == TOKEN_URL:
        return
    raise ReadOnlyViolation(f"Blocked {method} {parts.path}: monitor is read-only (market data GETs only)")


class SchwabClient:
    def __init__(self, app_key: str, app_secret: str, token_file: Path, callback_url: str = "https://127.0.0.1"):
        if not app_key or not app_secret:
            raise RuntimeError("SCHWAB_APP_KEY / SCHWAB_APP_SECRET missing - copy .env.example to .env")
        self.app_key, self.app_secret = app_key, app_secret
        self.callback_url = callback_url
        self.token_file = Path(token_file)
        self.http = GuardedSession()
        self.tokens = json.loads(self.token_file.read_text()) if self.token_file.exists() else None

    # ---------------- OAuth ----------------
    def _basic(self) -> str:
        return base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()

    def _save(self, tok: dict) -> None:
        tok["obtained_at"] = time.time()
        if "refresh_token_obtained_at" not in tok:
            tok["refresh_token_obtained_at"] = (self.tokens or {}).get("refresh_token_obtained_at", time.time())
        self.tokens = tok
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(tok, indent=2))
        try:
            self.token_file.chmod(0o600)
        except OSError:
            pass

    def authorize_url(self) -> str:
        return f"{AUTHORIZE_URL}?client_id={quote(self.app_key)}&redirect_uri={quote(self.callback_url, safe='')}"

    def exchange_code(self, code: str) -> None:
        r = self.http.post(TOKEN_URL, headers={"Authorization": f"Basic {self._basic()}",
                                               "Content-Type": "application/x-www-form-urlencoded"},
                           data={"grant_type": "authorization_code", "code": code,
                                 "redirect_uri": self.callback_url}, timeout=20)
        r.raise_for_status()
        tok = r.json()
        tok["refresh_token_obtained_at"] = time.time()
        self._save(tok)

    def _refresh(self) -> None:
        if not self.tokens or "refresh_token" not in self.tokens:
            raise RuntimeError("No Schwab tokens. Run:  python auth.py")
        r = self.http.post(TOKEN_URL, headers={"Authorization": f"Basic {self._basic()}",
                                               "Content-Type": "application/x-www-form-urlencoded"},
                           data={"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"]},
                           timeout=20)
        if r.status_code in (400, 401):
            raise RuntimeError("Schwab refresh token rejected (they expire after 7 days). Run:  python auth.py")
        r.raise_for_status()
        tok = r.json()
        tok.setdefault("refresh_token", self.tokens["refresh_token"])
        tok["refresh_token_obtained_at"] = self.tokens.get("refresh_token_obtained_at", time.time())
        self._save(tok)

    def _access_token(self) -> str:
        if not self.tokens:
            raise RuntimeError("No Schwab tokens. Run:  python auth.py")
        age = time.time() - self.tokens.get("obtained_at", 0)
        if age > self.tokens.get("expires_in", 1800) - 120:
            self._refresh()
        return self.tokens["access_token"]

    # ---------------- market data (GET only) ----------------
    def _get(self, path: str, params: dict) -> dict:
        if not path.startswith(MARKETDATA_PREFIX):
            raise ReadOnlyViolation(path)
        for attempt in range(3):
            headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
            try:
                r = self.http.get(BASE + path, params=params, headers=headers, timeout=15)
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {path} failed")

    def quotes(self, symbols: list[str]) -> dict:
        return self._get(f"{MARKETDATA_PREFIX}quotes",
                         {"symbols": ",".join(symbols), "fields": "quote,reference", "indicative": "false"})

    def price_history(self, symbol: str, minutes: int, days: int = 3) -> dict:
        return self._get(f"{MARKETDATA_PREFIX}pricehistory",
                         {"symbol": symbol, "periodType": "day", "period": days,
                          "frequencyType": "minute", "frequency": minutes,
                          "needExtendedHoursData": "false", "needPreviousClose": "false"})
