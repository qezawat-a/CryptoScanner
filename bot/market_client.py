"""Market data — PERP ONLY (futures), bedune API key. Spot kolan hazf shode.

Chain: binance_futures -> bybit_linear -> mexc_swap -> gate_futures -> okx_swap -> bitget_mix.
Har symbol avalin source ke perp dashte bashe lock mishe (per-symbol).
3m vaghti source nadare (mexc/gate) az 1m sakhte mishe (resample vagheie).
Khuruji: oldest-first [{timestamp(ms), open, high, low, close, volume}].
"""

import logging
import time
import requests

logger = logging.getLogger("market_client")

FAPI = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
MEXC_SWAP = "https://contract.mexc.com"
GATE_FUT = "https://api.gateio.ws"
OKX = "https://www.okx.com"
BITGET = "https://api.bitget.com"
BINGX = "https://open-api.bingx.com"

SOURCES = ("binance_futures", "bybit_linear", "mexc_swap",
           "gate_futures", "okx_swap", "bitget_mix", "bingx_swap")

ALL_TF = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}
NATIVE = {
    "binance_futures": set(ALL_TF),
    "bybit_linear": set(ALL_TF),
    "mexc_swap": {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
    "gate_futures": {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"},
    "okx_swap": set(ALL_TF),
    "bitget_mix": {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
    "bingx_swap": {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
}
RESAMPLE = {"3m": 3}  # 3m = 3 x 1m (vagheie, na copy)

BYBIT_IV = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W"}
MEXC_IV = {"1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30",
           "1h": "Min60", "4h": "Hour4", "1d": "Day1", "1w": "Week1"}
GATE_IV = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d", "1w": "1w"}
OKX_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D", "1w": "1W"}
BITGET_GRAN = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
               "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week"}


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


def _under(sym: str) -> str:
    return sym[:-4] + "_" + sym[-4:] if sym.endswith("USDT") else sym


def _dash(sym: str) -> str:
    return sym[:-4] + "-" + sym[-4:] if sym.endswith("USDT") else sym


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
                native = NATIVE.get(src, set())
                if interval in native:
                    out = getattr(self, f"_k_{src}")(sym, interval, limit)
                elif interval in RESAMPLE:
                    out = self._resampled(src, sym, interval, limit)
                else:
                    continue
                out = sorted((c for c in out if c.get("close", 0) > 0),
                             key=lambda c: c["timestamp"])
                if len(out) >= 40:
                    if self._is_dead(out, interval):
                        logger.warning(f"{src} dead data {sym} {interval} (flat/stale) -> next")
                        self._source.pop(sym, None)
                        continue
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
        if self.get_price(sym) <= 0:
            return False, "?", 0.0
        # vitality confirm: source e morde (flat/stale) relock mishe ru live
        if not self.get_klines(sym, "1m", limit=45):
            return False, "?", 0.0
        return True, self.last_source(sym), self.get_price(sym)

    def _order(self, sym: str) -> list:
        prefer = self._source.get(sym)
        order = [prefer] if prefer else []
        return order + [s for s in SOURCES if s not in order]

    @staticmethod
    def _is_dead(candles: list, interval: str) -> bool:
        """Delist/freeze: hame close yeksan ya candle jadid nayumade."""
        try:
            closes = [float(c["close"]) for c in candles[-60:]]
            if max(closes) - min(closes) <= 0:
                return True
        except Exception:
            pass
        try:
            unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
            sec = int(interval[:-1]) * unit.get(interval[-1:], 60)
            ts = float(candles[-1]["timestamp"])
            if ts < 1e12:
                ts *= 1000
            if time.time() * 1000 - ts > sec * 1000 * 3:
                return True
        except Exception:
            pass
        return False

    def _get(self, url: str, params: dict = None):
        r = self._s.get(url, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------- 3m resample az 1m ----------

    def _resampled(self, src: str, sym: str, interval: str, limit: int) -> list:
        mult = RESAMPLE[interval]
        raw = getattr(self, f"_k_{src}")(sym, "1m", min(limit * mult + 10, 1000))
        raw = sorted(raw, key=lambda c: c["timestamp"])
        # candle e zende e 1m ro bendaz dur (mesle scanner)
        if raw:
            ts = float(raw[-1]["timestamp"])
            ts = ts * 1000 if ts < 1e12 else ts
            if time.time() * 1000 < ts + 60 * 1000:
                raw = raw[:-1]
        out = []
        for i in range(0, len(raw) - mult + 1, mult):
            grp = raw[i:i + mult]
            if len(grp) < mult:
                continue
            out.append({"timestamp": grp[0]["timestamp"], "open": grp[0]["open"],
                        "high": max(c["high"] for c in grp),
                        "low": min(c["low"] for c in grp),
                        "close": grp[-1]["close"],
                        "volume": sum(c["volume"] for c in grp)})
        return out

    # ---------- Binance futures ----------

    def _k_binance_futures(self, sym: str, interval: str, limit: int) -> list:
        try:
            rows = self._get(f"{FAPI}/fapi/v1/klines",
                             {"symbol": sym, "interval": interval, "limit": min(limit, 1500)})
        except Exception as e:
            if "400" in str(e):
                raise ValueError(f"symbol {sym} nist")
            raise
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_binance_futures(self, sym: str) -> float:
        try:
            j = self._get(f"{FAPI}/fapi/v1/ticker/price", {"symbol": sym})
        except Exception as e:
            if "400" in str(e):
                raise ValueError("no symbol")
            raise
        return float(j.get("price") or 0)

    # ---------- Bybit linear ----------

    def _k_bybit_linear(self, sym: str, interval: str, limit: int) -> list:
        j = self._get(f"{BYBIT}/v5/market/kline",
                      {"category": "linear", "symbol": sym,
                       "interval": BYBIT_IV[interval], "limit": min(limit, 1000)})
        if j.get("retCode") != 0:
            raise ValueError(f"Bybit: {j.get('retMsg')}")
        rows = (j.get("result") or {}).get("list") or []
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_bybit_linear(self, sym: str) -> float:
        j = self._get(f"{BYBIT}/v5/market/tickers", {"category": "linear", "symbol": sym})
        lst = ((j.get("result") or {}).get("list")) or []
        if not lst:
            raise ValueError("no symbol")
        return float(lst[0].get("lastPrice") or 0)

    # ---------- MEXC swap ----------

    def _k_mexc_swap(self, sym: str, interval: str, limit: int) -> list:
        j = self._get(f"{MEXC_SWAP}/api/v1/contract/kline/{_under(sym)}",
                      {"interval": MEXC_IV[interval]})
        d = (j.get("data") if isinstance(j, dict) else None) or {}
        n = len(d.get("time") or [])
        if j.get("code") != 0 or n == 0:
            raise ValueError("no symbol")
        return [{"timestamp": float(d["time"][i]) * 1000, "open": float(d["open"][i]),
                 "high": float(d["high"][i]), "low": float(d["low"][i]),
                 "close": float(d["close"][i]), "volume": float(d["vol"][i])}
                for i in range(n)][-limit:]

    def _p_mexc_swap(self, sym: str) -> float:
        j = self._get(f"{MEXC_SWAP}/api/v1/contract/ticker", {"symbol": _under(sym)})
        d = (j.get("data") if isinstance(j, dict) else None) or {}
        if j.get("code") != 0 or not d:
            raise ValueError("no symbol")
        return float(d.get("lastPrice") or 0)

    # ---------- Gate futures ----------

    def _k_gate_futures(self, sym: str, interval: str, limit: int) -> list:
        rows = self._get(f"{GATE_FUT}/api/v4/futures/usdt/candlesticks",
                         {"contract": _under(sym), "interval": GATE_IV[interval],
                          "limit": min(limit, 2000)})
        if not rows:
            raise ValueError("no symbol")
        return [{"timestamp": float(k["t"]) * 1000, "open": float(k["o"]),
                 "high": float(k["h"]), "low": float(k["l"]),
                 "close": float(k["c"]), "volume": float(k["v"])} for k in rows]

    def _p_gate_futures(self, sym: str) -> float:
        rows = self._get(f"{GATE_FUT}/api/v4/futures/usdt/tickers", {"contract": _under(sym)})
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("last") or 0)

    # ---------- OKX swap ----------

    def _k_okx_swap(self, sym: str, interval: str, limit: int) -> list:
        j = self._get(f"{OKX}/api/v5/market/candles",
                      {"instId": _dash(sym) + "-SWAP", "bar": OKX_BAR[interval],
                       "limit": min(limit, 300)})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_okx_swap(self, sym: str) -> float:
        j = self._get(f"{OKX}/api/v5/market/ticker", {"instId": _dash(sym) + "-SWAP"})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("last") or 0)

    # ---------- Bitget mix ----------

    def _k_bitget_mix(self, sym: str, interval: str, limit: int) -> list:
        j = self._get(f"{BITGET}/api/v2/mix/market/candles",
                      {"symbol": sym, "granularity": BITGET_GRAN[interval],
                       "productType": "USDT-FUTURES", "limit": min(limit, 1000)})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return [{"timestamp": float(k[0]), "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])} for k in rows]

    def _p_bitget_mix(self, sym: str) -> float:
        j = self._get(f"{BITGET}/api/v2/mix/market/ticker",
                      {"symbol": sym, "productType": "USDT-FUTURES"})
        rows = j.get("data") or []
        if not rows:
            raise ValueError("no symbol")
        return float(rows[0].get("lastPr") or 0)

    # ---------- BingX swap (perp) ----------

    def _k_bingx_swap(self, sym: str, interval: str, limit: int) -> list:
        j = self._get(f"{BINGX}/openApi/swap/v3/quote/klines",
                      {"symbol": _dash(sym), "interval": interval, "limit": min(limit, 1440)})
        rows = j.get("data") or []
        if j.get("code") != 0 or not rows:
            raise ValueError(f"BingX swap: {j.get('msg')}")
        return [{"timestamp": float(k["time"]), "open": float(k["open"]),
                 "high": float(k["high"]), "low": float(k["low"]),
                 "close": float(k["close"]), "volume": float(k["volume"])} for k in rows]

    def _p_bingx_swap(self, sym: str) -> float:
        kl = self._k_bingx_swap(sym, "1m", 2)
        if not kl:
            raise ValueError("no symbol")
        return float(kl[-1]["close"])
