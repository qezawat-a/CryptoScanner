"""Market data: Binance public (primary) + Bybit public (fallback).
Hich API key lazem nadare. Symbol ha be format yekdas: BTCUSDT.
Vorudi az user ghabul mikone: saga_usdt, SAGAUSDT, saga, SAGA/USDT...
"""

import logging
import requests

logger = logging.getLogger("market_client")

BINANCE = "https://api.binance.com"
BYBIT = "https://api.bybit.com"

BYBIT_INTERVAL = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W",
}


def normalize_symbol(raw: str) -> str:
    s = (raw or "").upper().strip().replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    if not s:
        return ""
    if s.endswith("USDT"):
        return s
    if s.endswith("USD"):
        return s[:-3] + "USDT"
    return s + "USDT"


def to_display(sym: str) -> str:
    """BTCUSDT -> btc_usdt (mesle XT, baraye namayesh)."""
    s = normalize_symbol(sym).lower()
    if s.endswith("_usdt"):
        return s
    if s.endswith("usdt"):
        return s[:-4] + "_usdt"
    return s


class MarketClient:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._source = {}  # symbol -> "binance" | "bybit"
        self._s = requests.Session()

    # ---------- klines ----------

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """Return list of dicts: {timestamp(ms), open, high, low, close, volume}.
        Oldest-first. Empty list on failure."""
        sym = normalize_symbol(symbol)
        prefer = self._source.get(sym)
        order = [prefer] if prefer else []
        order += [s for s in ("binance", "bybit") if s not in order]
        for src in order:
            try:
                if src == "binance":
                    out = self._binance_klines(sym, interval, limit)
                else:
                    out = self._bybit_klines(sym, interval, limit)
                if out and len(out) >= 40:
                    self._source[sym] = src
                    return out
            except Exception as e:
                logger.warning(f"{src} kline fail {sym} {interval}: {e}")
        return []

    def _binance_klines(self, sym: str, interval: str, limit: int) -> list:
        r = self._s.get(f"{BINANCE}/api/v3/klines",
                        params={"symbol": sym, "interval": interval, "limit": min(limit, 1000)},
                        timeout=self.timeout)
        if r.status_code == 400:
            raise ValueError(f"Binance: symbol {sym} nist")
        r.raise_for_status()
        out = []
        for k in r.json():
            out.append({"timestamp": float(k[0]), "open": float(k[1]),
                        "high": float(k[2]), "low": float(k[3]),
                        "close": float(k[4]), "volume": float(k[5])})
        return out

    def _bybit_klines(self, sym: str, interval: str, limit: int) -> list:
        iv = BYBIT_INTERVAL.get(interval)
        if not iv:
            raise ValueError(f"interval {interval} unsupported")
        r = self._s.get(f"{BYBIT}/v5/market/kline",
                        params={"category": "spot", "symbol": sym,
                                "interval": iv, "limit": min(limit, 1000)},
                        timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if j.get("retCode") != 0:
            raise ValueError(f"Bybit: {j.get('retMsg')}")
        rows = (j.get("result") or {}).get("list") or []
        out = []
        for k in reversed(rows):  # bybit newest-first -> oldest-first
            out.append({"timestamp": float(k[0]), "open": float(k[1]),
                        "high": float(k[2]), "low": float(k[3]),
                        "close": float(k[4]), "volume": float(k[5])})
        return out

    # ---------- price ----------

    def get_price(self, symbol: str) -> float:
        sym = normalize_symbol(symbol)
        prefer = self._source.get(sym)
        order = [prefer] if prefer else []
        order += [s for s in ("binance", "bybit") if s not in order]
        for src in order:
            try:
                if src == "binance":
                    r = self._s.get(f"{BINANCE}/api/v3/ticker/price",
                                    params={"symbol": sym}, timeout=self.timeout)
                    if r.status_code == 400:
                        raise ValueError("no symbol")
                    r.raise_for_status()
                    p = float(r.json().get("price") or 0)
                else:
                    r = self._s.get(f"{BYBIT}/v5/market/tickers",
                                    params={"category": "spot", "symbol": sym},
                                    timeout=self.timeout)
                    r.raise_for_status()
                    lst = ((r.json().get("result") or {}).get("list")) or []
                    p = float((lst[0].get("lastPrice") if lst else 0) or 0)
                if p > 0:
                    self._source[sym] = src
                    return p
            except Exception as e:
                logger.warning(f"{src} price fail {sym}: {e}")
        return 0.0

    def last_source(self, symbol: str) -> str:
        return self._source.get(normalize_symbol(symbol), "?")

    def validate(self, symbol: str) -> tuple:
        """(ok: bool, source: str, price: float)"""
        sym = normalize_symbol(symbol)
        if not sym:
            return False, "?", 0.0
        price = self.get_price(sym)
        if price > 0:
            return True, self.last_source(sym), price
        return False, "?", 0.0
