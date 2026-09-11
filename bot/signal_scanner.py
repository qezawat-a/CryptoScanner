"""SignalScanner — mesle XT vali generic (bedune pandas, bedune XT).

- forming candle drop (mesle XT)
- multi-timeframe weights mesle XT
- confidence formula mesle XT: alignment * (60 + 40*win_conf)
"""

import logging
import time

from .market_client import MarketClient
from .strategies import StrategyEngine

logger = logging.getLogger("scanner")

VALID_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]

TF_WEIGHTS = {"1m": 0.5, "3m": 0.8, "5m": 1.0, "15m": 1.5, "30m": 2.0,
              "1h": 2.5, "2h": 2.8, "4h": 3.0, "1d": 4.0, "1w": 5.0}

UNIT = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _flat_or_stale(candles: list, interval: str) -> str:
    """Data e delist/freeze شده: hich signal nade. Returns reason or ''."""
    try:
        seconds = int(interval[:-1]) * UNIT.get(interval[-1:], 60)
    except Exception:
        seconds = 60
    try:
        last_ts = float(candles[-1]["timestamp"])
        if last_ts < 1e12:
            last_ts *= 1000
        if time.time() * 1000 - last_ts > seconds * 1000 * 3:
            return "stale_data"
    except Exception:
        pass
    try:
        closes = [float(c["close"]) for c in candles[-60:]]
        if max(closes) - min(closes) <= 0:
            return "flat_data"
    except Exception:
        pass
    return ""


def drop_forming_candle(candles: list, interval: str) -> list:
    try:
        seconds = int(interval[:-1]) * UNIT.get(interval[-1:], 60)
        last_ts = float(candles[-1]["timestamp"])
        if last_ts < 1e12:
            last_ts *= 1000
        if time.time() * 1000 < last_ts + seconds * 1000:
            return candles[:-1]
    except Exception:
        pass
    return candles


class SignalScanner:
    def __init__(self, client: MarketClient, settings: dict):
        self.client = client
        self.settings = settings
        self.engine = StrategyEngine()

    def _intervals(self) -> list:
        raw = self.settings.get("timeframes", "1m,3m,5m,15m")
        if isinstance(raw, str):
            raw = raw.split(",")
        out = [t.strip().lower() for t in raw if t.strip().lower() in VALID_INTERVALS]
        return out or ["1m", "3m", "5m", "15m"]

    def scan_single_timeframe(self, symbol: str, interval: str) -> dict:
        tf_min = int(self.settings.get("tf_min_confidence", 70))
        min_agree = int(self.settings.get("min_agreeing_strategies", 2))
        candles = self.client.get_klines(symbol, interval, limit=200)
        if not candles or len(candles) < 40:
            return {"direction": "NEUTRAL", "confidence": 0, "all_signals": [],
                    "strategies_used": [], "error": "insufficient_data"}
        candles = drop_forming_candle(candles, interval)
        if len(candles) < 40:
            return {"direction": "NEUTRAL", "confidence": 0, "all_signals": [],
                    "strategies_used": [], "error": "insufficient_data"}
        bad = _flat_or_stale(candles, interval)
        if bad:
            return {"direction": "NEUTRAL", "confidence": 0, "all_signals": [],
                    "strategies_used": [], "error": bad}
        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        return self.engine.get_consensus(closes, volumes, tf_min, min_agree)

    def scan_multi_timeframe(self, symbol: str) -> dict:
        min_conf = int(self.settings.get("min_confidence", 80))
        intervals = self._intervals()
        all_results = {}
        long_w = short_w = voted_w = 0.0
        strats = set()
        for tf in intervals:
            r = self.scan_single_timeframe(symbol, tf)
            all_results[tf] = r
            if r["direction"] == "NEUTRAL":
                continue
            w = TF_WEIGHTS.get(tf, 1.0)
            voted_w += w
            contrib = w * (r["confidence"] / 100.0)
            if r["direction"] == "LONG":
                long_w += contrib
            else:
                short_w += contrib
            strats.update(r.get("strategies_used", []))

        overall, strength, conf = "NEUTRAL", 0.0, 0
        if voted_w > 0 and long_w != short_w:
            if long_w > short_w:
                overall, winner, loser = "LONG", long_w, short_w
            else:
                overall, winner, loser = "SHORT", short_w, long_w
            strength = (winner - loser) / voted_w
            total_w = sum(TF_WEIGHTS.get(t, 1.0) for t in intervals)
            aligned_w = sum(TF_WEIGHTS.get(t, 1.0) for t, r in all_results.items()
                            if r.get("direction") == overall)
            if (str(self.settings.get("alignment_mode", "strict")).lower() == "loose"
                    and overall != "NEUTRAL"):
                # TF e NEUTRAL vali ham-jahat (lean) nesf vazn migire.
                # Mesal 15m ke MOMENTUM LONG dare vali tak-strategy block شده.
                gate = int(self.settings.get("tf_min_confidence", 70))
                for t, r in all_results.items():
                    if r.get("direction") != "NEUTRAL" or r.get("error"):
                        continue
                    l_score = sum(s["confidence"] for s in r.get("all_signals", [])
                                  if s["direction"] == "LONG" and s["confidence"] >= gate)
                    s_score = sum(s["confidence"] for s in r.get("all_signals", [])
                                  if s["direction"] == "SHORT" and s["confidence"] >= gate)
                    lean = ("LONG" if l_score > s_score else
                            "SHORT" if s_score > l_score else None)
                    if lean == overall:
                        aligned_w += TF_WEIGHTS.get(t, 1.0) * 0.5
            alignment = aligned_w / total_w if total_w else 0.0
            win_conf = winner / voted_w if voted_w else 0.0
            conf = int(alignment * (60 + 40 * win_conf))

        price = self.client.get_price(symbol)
        return {
            "direction": overall, "confidence": conf,
            "signal_strength": strength, "strategies_used": sorted(strats),
            "timeframe_results": all_results,
            "long_weight": long_w, "short_weight": short_w,
            "voted_weight": voted_w, "price": price, "symbol": symbol,
            "timestamp": time.time(), "min_conf": min_conf,
            "source": self.client.last_source(symbol),
        }

    def is_aligned(self, result: dict) -> bool:
        return (result["direction"] != "NEUTRAL"
                and result["confidence"] >= int(self.settings.get("min_confidence", 80)))

    def verdict(self, result: dict) -> str:
        """Jomle e sade va ghatei: hich LLM/user ghati nakone."""
        min_conf = int(self.settings.get("min_confidence", 80))
        if self.is_aligned(result):
            return (f"ALIGNED {result['direction']} {result['confidence']}% — "
                    f"signal motabar (balaye had {min_conf}%)")
        vetoes = [r.get("veto_reason") for r in result.get("timeframe_results", {}).values()
                  if r.get("veto_reason")]
        if vetoes:
            return f"SABR — veto: {vetoes[0]}"
        if result["direction"] != "NEUTRAL":
            return (f"SABR — {result['direction']} zaif "
                    f"({result['confidence']}% < had {min_conf}%)")
        gate = int(self.settings.get("tf_min_confidence", 70))
        fired = any(s["direction"] != "NEUTRAL" and s["confidence"] >= gate
                    for r in result.get("timeframe_results", {}).values()
                    if not r.get("error")
                    for s in r.get("all_signals", []))
        if fired:
            return "SABR — signal ha tak‌تک و parakande‌ست (min_agree nashod)"
        return "SABR — hich signal e motabari nist"

    def format_report(self, result: dict) -> str:
        sym = result.get("symbol", "?")
        lines = [f"=== SCAN [{sym}] ({result.get('source', '?')}) ===",
                 f"Direction: {result['direction']}",
                 f"Confidence: {result['confidence']}% (min {result.get('min_conf', 80)}%)",
                 f"Strength: {result.get('signal_strength', 0):.2f}",
                 f"Price: {result.get('price', 0)}"]
        if result.get("strategies_used"):
            lines.append(f"Strategies: {', '.join(result['strategies_used'])}")
        gate = int(self.settings.get("tf_min_confidence", 70))
        for tf, r in result.get("timeframe_results", {}).items():
            if r.get("error"):
                lines.append(f"  {tf}: no data ({r['error']})")
                continue
            fired, below = [], []
            for s in r.get("all_signals", []):
                if s["direction"] == "NEUTRAL":
                    continue
                e = f"{s['strategy']}={s['direction']}({s['confidence']}%)"
                (below if s["confidence"] < gate else fired).append(e)
            parts = []
            if fired:
                parts.append("FIRED: " + ", ".join(fired))
            if below:
                parts.append(f"IGNORED (zire gate {gate}%, BI ASAR): {', '.join(below)}")
            lines.append(f"  {tf}: {r['direction']} ({r['confidence']}%) [{(' | '.join(parts)) or 'no fire'}]")
            if r.get("veto_reason"):
                lines.append(f"      veto: {r['veto_reason']}")
            if r.get("rsi") is not None:
                lines.append(f"      RSI: {r['rsi']:.1f}")
        lines.append(f"Long: {result.get('long_weight', 0):.2f} | Short: {result.get('short_weight', 0):.2f}")
        lines.append(f"VERDICT: {self.verdict(result)}")
        if self.is_aligned(result):
            lines.append(f"\n>>> ALIGNED {result['direction']} — baraye KCEX dasti <<<")
        return "\n".join(lines)
