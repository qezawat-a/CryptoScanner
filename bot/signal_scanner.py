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

        # RSI-FORCE (user principle): any timeframe with an
        # extreme RSI forces the OVERALL direction.
        # Among opposing extremes the winner is max(TF_weight * |rsi-50|).
        # Confidence stays HONEST so the 80 gate still protects entries.
        rsi_force = None
        cands = []
        for tf, r in all_results.items():
            if r.get("direction") not in ("LONG", "SHORT"):
                continue
            sigs = r.get("all_signals", []) or []
            rsi_sig = next((s for s in sigs if s.get("strategy") == "RSI"), None)
            det = (rsi_sig.get("details", {}) or {}) if rsi_sig else {}
            if rsi_sig and rsi_sig.get("direction") == r["direction"] and det.get("rsi") is not None:
                rv = float(det["rsi"])
                cands.append((TF_WEIGHTS.get(tf, 1.0) * abs(rv - 50), tf, rv, r["direction"]))
        if cands:
            _, ftf, frsi, fside = max(cands)
            if overall != fside:
                prev = overall
                overall = fside
                total_w = sum(TF_WEIGHTS.get(t, 1.0) for t in intervals)
                aligned_w = sum(TF_WEIGHTS.get(t, 1.0) for t, r in all_results.items()
                                if r.get("direction") == fside)
                fw = short_w if fside == "SHORT" else long_w
                ow = long_w if fside == "SHORT" else short_w
                win_conf = fw / voted_w if voted_w else 0.0
                conf = int((aligned_w / total_w) * (60 + 40 * win_conf)) if total_w else 0
                strength = (fw - ow) / voted_w if voted_w else 0.0
                rsi_force = (f"{ftf} RSI {frsi:.1f} {fside} overrules MTF (was {prev})")

        price = self.client.get_price(symbol)
        return {
            "direction": overall, "confidence": conf,
            "signal_strength": strength, "strategies_used": sorted(strats),
            "timeframe_results": all_results,
            "long_weight": long_w, "short_weight": short_w,
            "voted_weight": voted_w, "price": price, "symbol": symbol,
            "timestamp": time.time(), "min_conf": min_conf,
            "source": self.client.last_source(symbol),
            "rsi_force": rsi_force,
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
        if result.get("rsi_force"):
            return f"FORCE — {result['rsi_force']} (conf {result['confidence']}% < had {min_conf}%)"
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

    def _lean_token(self, s: dict, counted: set) -> str:
        name = s.get("strategy", "?")
        det = s.get("details", {}) or {}
        mark = "*" if s.get("direction") in ("LONG", "SHORT") and name in counted else ""
        if name == "RSI" and det.get("rsi") is not None:
            lean = det.get("lean")
            lc = det.get("lean_conf", 0) or 0
            if lean and lean != "NEUTRAL":
                return f"RSI:{det['rsi']:.1f} {lean}({lc}%){mark}"
            return f"RSI:{det['rsi']:.1f}{mark}"
        lean = det.get("lean")
        lc = det.get("lean_conf", 0) or 0
        if lean and lean != "NEUTRAL":
            return f"{name}={lean}({lc}%){mark}"
        if s.get("direction") in ("LONG", "SHORT"):
            return f"{name}={s['direction']}({s.get('confidence', 0)}%){mark}"
        if not det:
            return f"{name}=n/a"
        return f"{name}=flat"

    def _tf_line(self, tf: str, r: dict, gate: int) -> str:
        if r.get("error"):
            return f"  {tf}: no data ({r['error']})"
        sigs = r.get("all_signals", []) or []
        counted = set(r.get("strategies_used", []))
        toks = [self._lean_token(s, counted) for s in sigs]
        if r.get("direction") != "NEUTRAL":
            return f"  {tf}: {r['direction']} ({r['confidence']}%) [{' | '.join(toks)}]"
        leans = [(s.get("details", {}).get("lean_conf", 0) or 0,
                  s.get("details", {}).get("lean"), s.get("strategy", "?"))
                 for s in sigs
                 if (s.get("details", {}) or {}).get("lean") not in (None, "NEUTRAL")]
        if leans:
            conf, side, name = max(leans)
            return f"  {tf}: NEUTRAL (lean {side} {conf}% via {name}) [{' | '.join(toks)}]"
        return f"  {tf}: NEUTRAL (flat) [{' | '.join(toks)}]"

    def _top_lean(self, tfs: dict):
        best = None
        for tf, r in (tfs or {}).items():
            if r.get("error"):
                continue
            if r.get("direction") in ("LONG", "SHORT"):
                cand = (r.get("confidence", 0), f"{tf} TF vote", r["direction"])
                if best is None or cand[0] > best[0]:
                    best = cand
            for s in r.get("all_signals", []) or []:
                det = s.get("details", {}) or {}
                if det.get("lean") not in (None, "NEUTRAL"):
                    cand = (det.get("lean_conf", 0) or 0, f"{tf} {s.get('strategy','?')}", det["lean"])
                    if best is None or cand[0] > best[0]:
                        best = cand
        return best

    def format_report(self, result: dict) -> str:
        sym = result.get("symbol", "?")
        lines = [f"=== SCAN [{sym}] ({result.get('source', '?')}) ==="]
        if result.get("rsi_force"):
            lines.append(f"FORCE: {result['rsi_force']}")
        top = self._top_lean(result.get("timeframe_results", {}))
        if result["direction"] != "NEUTRAL":
            lines.append(f"Direction: {result['direction']}")
            lines.append(f"Confidence: {result['confidence']}% (min {result.get('min_conf', 80)}%)")
            lines.append(f"Strength: {result.get('signal_strength', 0):.2f}")
        else:
            if top:
                lines.append(f"Direction: NEUTRAL (top lean {top[2]} {top[0]}% - {top[1]})")
            else:
                lines.append("Direction: NEUTRAL (flat - no lean data)")
        if result.get("veto_reason"):
            lines.append(f"VETO: {result['veto_reason']}")
        lines.append(f"Price: {result.get('price', 0)}")
        if result.get("strategies_used"):
            lines.append(f"Strategies: {', '.join(result['strategies_used'])}")
        else:
            gate = int(self.settings.get("tf_min_confidence", 70))
            lines.append(f"Strategies: none counted (all below {gate}% gate - leans per TF below)")
        gate = int(self.settings.get("tf_min_confidence", 70))
        for tf, r in result.get("timeframe_results", {}).items():
            lines.append(self._tf_line(tf, r, gate))
        lw = result.get("long_weight", 0)
        sw = result.get("short_weight", 0)
        vw = result.get("voted_weight", 0)
        if vw > 0:
            parts = []
            if lw > 0:
                parts.append(f"Long: {lw:.2f}")
            else:
                parts.append("no LONG votes")
            if sw > 0:
                parts.append(f"Short: {sw:.2f}")
            else:
                parts.append("no SHORT votes")
            parts.append(f"Voted: {vw:.2f}")
            lines.append(" | ".join(parts))
        else:
            lines.append("No counted votes - every TF below gate (see leans above).")
        if self.is_aligned(result):
            lines.append(f"\n>>> ALIGNED {result['direction']} — baraye KCEX dasti <<<")
        return "\n".join(lines)
