"""Market data — multi-source public, bedune API key.

Chain: Binance -> Bybit(spot) -> Bybit(linear) -> MEXC -> Gate -> OKX -> KuCoin -> Bitget.
Har symbol avalin source ke dashte bashe lock mishe (per-symbol).
Vorudi: saga_usdt, SAGAUSDT, saga, SAGA/USDT... -> format har source.
Khuruji klines: oldest-first [{timestamp(ms), open, high, low, close, volume}].
"""

import logging
import requests

logger = logging.getLogger("market_client")

BINANCE = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
MEXC = "https://api.mexc.com"
BYBIT = "https://api.bybit.com"
GATE = "https://api.gateio.ws"
OKX = "https://www.okx.com"
KUCOIN = "https://api.kucoin.com"
BITGET = "https://api.bitget.com"

BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
                  "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W"}
GATE_INTERVAL = {"1m": "1m", "3m": "5m", "5m": "5m", "15m": "15m", "30m": "30m",
                 "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d", "1w": "7d"}
OKX_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D", "1w": "1W"}
KUCOIN_TYPE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
               "30m": "30min", "1h": "1hour", "2h": "2hour", "4h": "4hour",
               "1d": "1day", "1w": "1week"}
BITGET_GRAN = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
               "1h": "1h", "2h": "4h", "4h": "4h", "1d": "1day", "1w": "1week"}
MEXC_INTERVAL = {"1m": "1m", "3m": "5m", "5m": "5m", "15m": "15m", "30m": "30m",
                 "1h": "1h", "2h": "4h", "4h": "4h", "1d": "1D", "1w": "1W"}

SOURCES = ("binance", "binance_futures", "bybit_linear", "bybit_spot", "mexc", "gate", "okx", "kucoin", "bitget")


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
    s = normalize_symbol(sym).lower()
    if s.endswith("usdt"):
        return s[:-4] + "_usdt"
    return s


def _dash(sym: str) -> str:
    return sym[:-4] + "-" + sym[-4:] if sym.endswith("USDT") else sym


def _under(sym: str) -> str:
    return sym[:-4] + "_" + sym[-4:] if sym.endswith("USDT") else sym


class MarketClient:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self._source = {}
        self._s = requests.Session()

    # ---------- public ----------

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        sym = normalize_symbol(symbol)
        for src in self._order(sym):
            try:
                out = getattr(self, f"_k_{src}")(sym, interval, limit)
                out = sorted((c for c in out if c.get("close", 0) > 0),
                             key=lambda c: c["timestamp"])
                if len(out) >= 40:
                    self._source[sym] = src
                    return out
            except Exception as e:
                logger.warning(f"{src} kline fail {sym} {interval}: {e}")
        return []

    def get_price(self, symbol: str) -> float:
        sym = normalize_symbol(symbol)
        for src in self._order(sym):
            try:
                p = getattr(self, f"_p_{src}")(sym)
                if p > 0:
                    self._source[sym] = src
                    return p
            except Exception as e:
                logger.warning(f"{src} price fail {sym}: {e}")
        return 0.0

    def last_source(self, symbol: str) -> str:
        return self._source.get(normalize_symbol(symbol), "?")

    def validate(self, symbol: str) -> tuple:
        sym = normalize_symbol(symbol)
        if not sym:
            return False, "?", 0.0
        price = self.get_price(sym)
        if price > 0:
            return True, self.last_source(sym), price
        return False, "?", 0.0

    def _order(self, sym: str) -> list:
        prefer = self._source.get(sym)
        order = [prefer] if prefer else []
        return order + [s for s in SOURCES if s not in order]

    def _get(self, url: str, params: dict = None):
        r = self._s.get(url, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------- Binance / MEXC (same format; FAPI = perpetual futures) ----------

    def _binance_like_klines(self, base: str, sym: str, interval: str, limit: int,
                             prefix: str = "/api/v3") -> list:
        try:
            rows = self._get(f"{base}{prefix}/klines",
                             {"symbol": sym, "interval": interval, "limit": min(limit, 1000)})
        except Exception as e:
            if "400" in str(e):
                raise ValueError(f"symbol {sym} nist")
            raise
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _binance_like_price(self, base: str, sym: str, prefix: str = "/api/v3") -> float:
        try:
            j = self._get(f"{base}{prefix}/ticker/price", {"symbol": sym})
        except Exception as e:
            if "400" in str(e):
                raise ValueError("no symbol")
            raise
        return float(j.get("price") or 0)

    def _k_binance(self, s, i, n): return self._binance_like_klines(BINANCE, s, i, n)
    def _p_binance(self, s): return self._binance_like_price(BINANCE, s)
    def _k_binance_futures(self, s, i, n):
        return self._binance_like_klines(FAPI, s, i, n, prefix="/fapi/v1")
    def _p_binance_futures(self, s):
        return self._binance_like_price(FAPI, s, prefix="/fapi/v1")

    def _k_mexc(self, s, i, n):
        return self._binance_like_klines(MEXC, s, MEXC_INTERVAL.get(i, "1m"), n)
    def _p_mexc(self, s): return self._binance_like_price(MEXC, s)

    # ---------- Bybit ----------

    def _bybit_klines(self, cat: str, sym: str, interval: str, limit: int) -> list:
        iv = BYBIT_INTERVAL.get(interval)
        if not iv:
            raise ValueError("interval?")
        j = self._get(f"{BYBIT}/v5/market/kline",
                      {"category": cat, "symbol": sym, "interval": iv, "limit": min(limit, 1000)})
        if j.get("retCode") != 0:
            raise ValueError(f"Bybit: {j.get('retMsg')}")
        rows = (j.get("result") or {}).get("list") or []
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _bybit_price(self, cat: str, sym: str) -> float:
        j = self._get(f"{BYBIT}/v5/market/tickers", {"category": cat, "symbol": sym})
        lst = ((j.get("result") or {}).get("list")) or []
        if not lst:
            raise ValueError("no symbol")
        return float(lst[0].get("lastPrice") or 0)

    def _k_bybit_spot(self, s, i, n): return self._bybit_klines("spot", s, i, n)
    def _p_bybit_spot(self, s): return self._bybit_price("spot", s)
    def _k_bybit_linear(self, s, i, n): return self._bybit_klines("linear", s, i, n)
    def _p_bybit_linear(self, s): return self._bybit_price("linear", s)

    # ---------- Gate ----------

    def _k_gate(self, sym: str, interval: str, limit: int) -> list:
        iv = GATE_INTERVAL.get(interval, "1m")
        rows = self._get(f"{GATE}/api/v4/spot/candlesticks",
                         {"currency_pair": _under(sym), "interval": iv, "limit": min(limit, 2000)})
        if not rows:
            raise ValueError("no symbol")
        # [t(sec), volume, close, high, low, open]
        return [{"timestamp": float(k[0]) * 1000, "open": float(k[5]), "high": float(k[3]),
                 "low": float(k[4]), "close": float(k[2]), "volume": float(k[1])} for k in rows]

    def _p_gate(self, sym: str) -> float:
        rows = self._get(f"{GATE}/api/v4/spot/tickers", {"currency_pair": _under(sym)})
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("last") or 0)

    # ---------- OKX ----------

    def _k_okx(self, sym: str, interval: str, limit: int) -> list:
        bar = OKX_BAR.get(interval, "1m")
        j = self._get(f"{OKX}/api/v5/market/candles",
                      {"instId": _dash(sym), "bar": bar, "limit": min(limit, 300)})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        # [ts(ms), o,h,l,c, vol, ...]
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_okx(self, sym: str) -> float:
        j = self._get(f"{OKX}/api/v5/market/ticker", {"instId": _dash(sym)})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("last") or 0)

    # ---------- KuCoin ----------

    def _k_kucoin(self, sym: str, interval: str, limit: int) -> list:
        tp = KUCOIN_TYPE.get(interval, "1min")
        j = self._get(f"{KUCOIN}/api/v1/market/candles",
                      {"symbol": _dash(sym), "type": tp})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        # [time(sec), o,c,h,l, vol, turnover]
        return [{"timestamp": float(k[0]) * 1000, "open": float(k[1]), "high": float(k[3]),
                 "low": float(k[4]), "close": float(k[2]), "volume": float(k[5])} for k in rows]

    def _p_kucoin(self, sym: str) -> float:
        j = self._get(f"{KUCOIN}/api/v1/market/orderbook/level1", {"symbol": _dash(sym)})
        d = j.get("data")
        if not d:
            raise ValueError("no symbol")
        return float(d.get("price") or 0)

    # ---------- Bitget ----------

    def _k_bitget(self, sym: str, interval: str, limit: int) -> list:
        g = BITGET_GRAN.get(interval, "1m")
        j = self._get(f"{BITGET}/api/v2/spot/market/candles",
                      {"symbol": sym, "granularity": g, "limit": min(limit, 1000)})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        # [ts(ms), o,h,l,c, vol, ...]
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_bitget(self, sym: str) -> float:
        j = self._get(f"{BITGET}/api/v2/spot/market/tickers", {"symbol": sym})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("lastPr") or 0)
