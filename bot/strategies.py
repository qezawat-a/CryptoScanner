"""Pure-python strategy engine — port daghigh az CryptoMind-XT/bot/strategies.py.
Hich pandas nadare (sabok baraye Termux/Railway). Logic, param ha,
formula haye confidence, RSI veto, RSI-first, min_agree — hame mesle XT.
Tanhā farq: ewm/rolling ba حلقه pure-python (mesle pandas adjust=False).

FIXES APPLIED:
  1. RSI extreme HARD OVERRIDE: RSI>=70 → force SHORT (even if no other strat agrees)
                               RSI<=30 → force LONG  (even if no other strat agrees)
  2. avg_confidence now only averages the WINNING side (no cross-contamination)
  3. RSIStrategy ALWAYS returns RSI value in details (visible in reports even when neutral)
  4. Recompute scores AFTER rsi_vote clears opposing side (correct math)
  5. Always-on lean% for ALL strategies (display even when silent)
  6. RSI gate exception: RSI always passes conf gate when extreme
  7. RSI-FIRST vote fires regardless of confidence
"""
from typing import List, Dict, Tuple


def _ema(values: List[float], span: int) -> List[float]:
    k = 2.0 / (span + 1.0)
    out = []
    e = None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _rsi_wilder_ewm(closes: List[float], period: int) -> List[float]:
    alpha = 1.0 / period
    avg_gain, avg_loss = 0.0, 0.0
    rsis = []
    prev = closes[0]
    started = False
    for c in closes:
        if not started:
            started = True
            prev = c
            rsis.append(50.0)
            continue
        delta = c - prev
        prev = c
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = gain * alpha + avg_gain * (1 - alpha)
        avg_loss = loss * alpha + avg_loss * (1 - alpha)
        rs = avg_gain / (avg_loss if avg_loss != 0 else 1e-10)
        rsis.append(100 - (100 / (1 + rs)))
    return rsis


def _lean_info(lean: str, lean_conf: int) -> dict:
    return {"lean": lean, "lean_conf": lean_conf if lean != "NEUTRAL" else 0}


def _ema(values: List[float], span: int) -> List[float]:
    k = 2.0 / (span + 1.0)
    out = []
    e = None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _rsi_wilder_ewm(closes: List[float], period: int) -> List[float]:
    alpha = 1.0 / period
    avg_gain, avg_loss = 0.0, 0.0
    rsis = []
    prev = closes[0]
    started = False
    for c in closes:
        if not started:
            started = True
            prev = c
            rsis.append(50.0)
            continue
        delta = c - prev
        prev = c
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = gain * alpha + avg_gain * (1 - alpha)
        avg_loss = loss * alpha + avg_loss * (1 - alpha)
        rs = avg_gain / (avg_loss if avg_loss != 0 else 1e-10)
        rsis.append(100 - (100 / (1 + rs)))
    return rsis


class EMAStrategy:
    name = "EMA"

    def __init__(self, fast_period: int = 9, slow_period: int = 21):
        if fast_period >= slow_period:
            raise ValueError("fast must be < slow")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def calculate(self, closes: List[float]) -> Tuple[str, int, dict]:
        if len(closes) < self.slow_period + 10:
            return "NEUTRAL", 0, {}
        ef = _ema(closes, self.fast_period)
        es = _ema(closes, self.slow_period)
        prev_diff = ef[-2] - es[-2]
        curr_diff = ef[-1] - es[-1]
        lean = "LONG" if curr_diff > 0 else ("SHORT" if curr_diff < 0 else "NEUTRAL")
        lc = 0 if lean == "NEUTRAL" else max(1, min(99, int(min(100, abs(curr_diff) / max(closes[-1], 0.01) * 10000))))
        if prev_diff < 0 and curr_diff > 0:
            strength = min(100, abs(curr_diff) / max(closes[-1], 0.01) * 10000)
            return "LONG", min(95, int(60 + strength * 2)), {"fast": ef[-1], "slow": es[-1], **_lean_info("LONG", min(95, int(60 + strength * 2)))}
        if prev_diff > 0 and curr_diff < 0:
            strength = min(100, abs(curr_diff) / max(closes[-1], 0.01) * 10000)
            return "SHORT", min(95, int(60 + strength * 2)), {"fast": ef[-1], "slow": es[-1], **_lean_info("SHORT", min(95, int(60 + strength * 2)))}
        return "NEUTRAL", 0, _lean_info(lean, lc)


class MACDStrategy:
    name = "MACD"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if fast >= slow:
            raise ValueError("fast must be < slow")
        self.fast, self.slow, self.signal_period = fast, slow, signal

    def calculate(self, closes: List[float]) -> Tuple[str, int, dict]:
        if len(closes) < self.slow + self.signal_period + 10:
            return "NEUTRAL", 0, {}
        ef = _ema(closes, self.fast)
        es = _ema(closes, self.slow)
        macd = [a - b for a, b in zip(ef, es)]
        sig = _ema(macd, self.signal_period)
        hist = [m - s for m, s in zip(macd, sig)]
        prev_hist, curr_hist = hist[-2], hist[-1]
        close_px = abs(closes[-1])
        lean = "LONG" if curr_hist > 0 else ("SHORT" if curr_hist < 0 else "NEUTRAL")
        lc = 0 if lean == "NEUTRAL" else max(1, min(99, int(min(100, abs(curr_hist) / close_px * 50000))))
        if curr_hist > 0 and prev_hist < 0:
            strength = min(100, abs(curr_hist) / abs(closes[-1]) * 50000)
            return "LONG", min(90, int(55 + strength * 0.5)), {"histogram": curr_hist, **_lean_info("LONG", min(90, int(55 + strength * 0.5)))}
        if curr_hist < 0 and prev_hist > 0:
            strength = min(100, abs(curr_hist) / abs(closes[-1]) * 50000)
            return "SHORT", min(90, int(55 + strength * 0.5)), {"histogram": curr_hist, **_lean_info("SHORT", min(90, int(55 + strength * 0.5)))}
        if curr_hist > 0 and prev_hist > 0 and macd[-1] > macd[-2]:
            ts = abs(macd[-1]) / abs(closes[-1]) * 10000
            conf = min(85, int(55 + ts))
            return "LONG", conf, _lean_info("LONG", conf)
        if curr_hist < 0 and prev_hist < 0 and macd[-1] < macd[-2]:
            ts = abs(macd[-1]) / abs(closes[-1]) * 10000
            conf = min(85, int(55 + ts))
            return "SHORT", conf, _lean_info("SHORT", conf)
        return "NEUTRAL", 0, _lean_info(lean, lc)


class RSIStrategy:
    name = "RSI"

    def __init__(self, period: int = 14, oversold: int = 30, overbought: int = 70):
        self.period, self.oversold, self.overbought = period, oversold, overbought

    def calculate(self, closes: List[float]) -> Tuple[str, int, dict]:
        if len(closes) < self.period + 10:
            return "NEUTRAL", 0, {}
        r = _rsi_wilder_ewm(closes, self.period)
        prev_rsi, curr_rsi = r[-2], r[-1]
        # Always return current RSI value for visibility and override logic
        details = {"rsi": curr_rsi}

        if prev_rsi < self.oversold and curr_rsi > self.oversold:
            strength = min(100, (curr_rsi - self.oversold) * 2)
            conf = min(90, int(60 + strength * 1.5))
            return "LONG", conf, {"rsi": curr_rsi, **_lean_info("LONG", conf)}
        if prev_rsi > self.overbought and curr_rsi < self.overbought:
            strength = min(100, (self.overbought - curr_rsi) * 2)
            conf = min(90, int(60 + strength * 1.5))
            return "SHORT", conf, {"rsi": curr_rsi, **_lean_info("SHORT", conf)}
        if curr_rsi < self.oversold:
            strength = min(100, (self.oversold - curr_rsi) * 2)
            conf = min(90, int(60 + strength * 1.5)) - 10
            return "LONG", conf, {"rsi": curr_rsi, **_lean_info("LONG", conf)}
        if curr_rsi > self.overbought:
            strength = min(100, (curr_rsi - self.overbought) * 2)
            conf = min(90, int(60 + strength * 1.5)) - 10
            return "SHORT", conf, {"rsi": curr_rsi, **_lean_info("SHORT", conf)}
        lean = "LONG" if curr_rsi < 50 else ("SHORT" if curr_rsi > 50 else "NEUTRAL")
        lc = 0 if lean == "NEUTRAL" else max(1, min(90, int(abs(curr_rsi - 50) * 1.8)))
        return "NEUTRAL", 0, {"rsi": curr_rsi, **_lean_info(lean, lc)}


class MomentumStrategy:
    name = "MOMENTUM"

    def __init__(self, period: int = 10, threshold: float = 0.005):
        self.period, self.threshold = period, threshold

    def calculate(self, closes: List[float], volumes: List[float]) -> Tuple[str, int, dict]:
        p = self.period
        if len(closes) < p + 10:
            return "NEUTRAL", 0, {}
        curr_mom = closes[-1] / closes[-1 - p] - 1 if closes[-1 - p] else 0
        prev_mom = closes[-2] / closes[-2 - p] - 1 if closes[-2 - p] else 0
        w = volumes[-p:]
        avg_vol = sum(w) / len(w) if w else 0
        current_vol = volumes[-1]
        vol_surge = current_vol > avg_vol * 1.2 if avg_vol > 0 else False
        lean = "LONG" if curr_mom > 0 else ("SHORT" if curr_mom < 0 else "NEUTRAL")
        lc = 0 if lean == "NEUTRAL" else max(1, min(99, int(min(100, abs(curr_mom) * 800))))
        if curr_mom > self.threshold and prev_mom < self.threshold and vol_surge:
            strength = min(100, abs(curr_mom) * 1000)
            conf = min(90, int(55 + strength * 2))
            return "LONG", conf, {"momentum": curr_mom, **_lean_info("LONG", conf)}
        if curr_mom < -self.threshold and prev_mom > -self.threshold and vol_surge:
            strength = min(100, abs(curr_mom) * 1000)
            conf = min(90, int(55 + strength * 2))
            return "SHORT", conf, {"momentum": curr_mom, **_lean_info("SHORT", conf)}
        if curr_mom > self.threshold:
            strength = min(100, abs(curr_mom) * 800)
            conf = min(90, int(55 + strength * 2)) - 15
            return "LONG", conf, {"momentum": curr_mom, **_lean_info("LONG", conf)}
        if curr_mom < -self.threshold:
            strength = min(100, abs(curr_mom) * 800)
            conf = min(90, int(55 + strength * 2)) - 15
            return "SHORT", conf, {"momentum": curr_mom, **_lean_info("SHORT", conf)}
        return "NEUTRAL", 0, _lean_info(lean, lc)


class StrategyEngine:
    """Mesle XT: RSI veto + RSI-first + min_agree + confidence mass vote.

    FIX: RSI extreme now ACTIVELY forces the opposite direction (not just passive clear).
         When RSI>=70 and nothing else fires SHORT → engine forces SHORT.
         When RSI<=30 and nothing else fires LONG  → engine forces LONG.
    """

    def __init__(self):
        self.ema = EMAStrategy()
        self.macd = MACDStrategy()
        self.rsi = RSIStrategy()
        self.mom = MomentumStrategy()

    def calculate_all(self, closes: List[float], volumes: List[float]) -> list:
        out = []
        for name, fn in (
            ("EMA", lambda: self.ema.calculate(closes)),
            ("MACD", lambda: self.macd.calculate(closes)),
            ("RSI", lambda: self.rsi.calculate(closes)),
            ("MOMENTUM", lambda: self.mom.calculate(closes, volumes)),
        ):
            d, c, det = fn()
            out.append({"strategy": name, "direction": d, "confidence": c, "details": det})
        return out

    def get_consensus(self, closes: List[float], volumes: List[float],
                      min_confidence: int = 80, min_agree: int = 2) -> dict:
        results = self.calculate_all(closes, volumes)

        # ---- Step 1: extract RSI value for veto + later MTF force ----
        rsi_entry = next((r for r in results if r["strategy"] == "RSI"), None)
        rsi_val = None
        if rsi_entry and rsi_entry.get("details", {}).get("rsi") is not None:
            rsi_val = float(rsi_entry["details"]["rsi"])

        # ---- Step 2: filter signals by confidence gate ----
        # EXCEPT RSI: when extreme (>=70/<=30) it always passes
        # the gate so it can veto/force — a mid-zone RSI with conf
        # below gate stays filtered (it voted nothing).
        long_signals = [r for r in results
                        if r["direction"] == "LONG"
                        and (r["confidence"] >= min_confidence
                             or (r["strategy"] == "RSI" and rsi_val is not None
                                 and (rsi_val >= 70 or rsi_val <= 30)))]
        short_signals = [r for r in results
                         if r["direction"] == "SHORT"
                         and (r["confidence"] >= min_confidence
                              or (r["strategy"] == "RSI" and rsi_val is not None
                                  and (rsi_val >= 70 or rsi_val <= 30)))]

        # ---- Step 3: RSI extreme → clear opposing side (per-TF veto) ----
        if rsi_val is not None:
            if rsi_val >= 70:
                long_signals = []
            if rsi_val <= 30:
                short_signals = []

        # ---- Step 4: RSI-first vote (NO confidence gate — extreme always fires) ----
        rsi_vote = None
        if rsi_entry and rsi_entry.get("direction") in ("LONG", "SHORT"):
            rsi_vote = rsi_entry["direction"]
            if rsi_vote == "LONG":
                short_signals = []
            else:
                long_signals = []

        # ---- Step 5: compute scores AFTER all clearing ----
        long_score = sum(r["confidence"] for r in long_signals)
        short_score = sum(r["confidence"] for r in short_signals)
        total_score = long_score + short_score

        # ---- Step 6: decide direction ----
        direction = "NEUTRAL"
        signal_strength = 0.0
        strategies_used: List[str] = []
        veto_reason = None

        if rsi_vote == "LONG" and long_signals:
            direction = "LONG"
            strategies_used = [r["strategy"] for r in long_signals]
        elif rsi_vote == "SHORT" and short_signals:
            direction = "SHORT"
            strategies_used = [r["strategy"] for r in short_signals]
        elif rsi_vote is None:
            if long_score > short_score and len(long_signals) >= min_agree:
                direction = "LONG"
                strategies_used = [r["strategy"] for r in long_signals]
            elif short_score > long_score and len(short_signals) >= min_agree:
                direction = "SHORT"
                strategies_used = [r["strategy"] for r in short_signals]

        # ---- Step 7: signal strength ----
        if direction != "NEUTRAL" and total_score > 0:
            signal_strength = abs(long_score - short_score) / total_score

        # ---- Step 8: avg confidence — WINNING SIDE ONLY ----
        avg_confidence = 0
        if direction == "LONG" and long_signals:
            avg_confidence = int(sum(r["confidence"] for r in long_signals) / len(long_signals))
        elif direction == "SHORT" and short_signals:
            avg_confidence = int(sum(r["confidence"] for r in short_signals) / len(short_signals))

        return {
            "direction": direction,
            "confidence": avg_confidence if avg_confidence else 0,
            "signal_strength": signal_strength,
            "strategies_used": strategies_used,
            "all_signals": results,
            "long_count": len(long_signals),
            "short_count": len(short_signals),
            "rsi": rsi_val,
            "veto_reason": veto_reason,
            "rsi_force": None,
        }
