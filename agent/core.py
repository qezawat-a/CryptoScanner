"""ScannerAgent — ReAct loop mesle XT vali NO-TRADE.

- chat(): harf ba user + tool (scan/set_symbol/...)
- confirm_signal(scan_report): LLM second-opinion ru ALIGNED — veto/confim + payam e amade alert
"""

import json
import logging
import os
import re
import time

logger = logging.getLogger("scan_agent")

SOUL_PATH = os.path.join(os.path.dirname(__file__), "soul.md")
try:
    with open(SOUL_PATH, encoding="utf-8") as f:
        SOUL = f.read()
except Exception:
    SOUL = ""

SYSTEM_PROMPT = f"""{SOUL}

SCANNER CONTEXT (no-trade, manual KCEX):
- To SCANNER hasti, na trader. Hich order nemizari, balance/position nadari.
- Data: Binance public (primary) + Bybit fallback. Symbol format BTCUSDT.
- Engine: EMA(9,21)/MACD(12,26,9)/RSI(14,30,70)/MOMENTUM(10,0.005), TF weights mesle XT.
- RSI-FIRST: RSI fire (>= gate) → harfe aval, mokhalef discard. RSI saket → min_agree=2. Tak strategy = SABR.
- Veto: RSI>=70 → LONG veto; RSI<=30 → SHORT veto. Forming candle: faghat candle baste.
- TOOLS: get_status, scan_market, get_market_data, set_symbol, set_setting, remember, no_signal.
- Hich tool trade nadari — age kasi khast trade baz kone, begoo khodesh tu KCEX manual baz kone.
- Zaban: haman zabane user (Finglish/Persian/English). Kutah!
"""


class ScannerAgent:
    def __init__(self, tools, brain, settings: dict):
        self.tools = tools
        self.brain = brain
        self.settings = settings
        self.TOOLS = __import__("agent.tools", fromlist=["TOOLS"]).TOOLS
        self.TOOLS_AUTO = __import__("agent.tools", fromlist=["TOOLS_AUTO"]).TOOLS_AUTO
        from config import Config
        self.Config = Config

    def get_model_info(self) -> str:
        try:
            return self.brain.get_model_info()
        except Exception:
            return "?"

    def chat(self, user_message: str) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}]
        for _ in range(self.Config.AGENT_MAX_STEPS):
            try:
                content, tool_calls, _ = self.brain.chat(messages, tools=self.TOOLS, tool_choice="auto")
            except Exception as e:
                return f"Agent error: {e}"
            if not tool_calls:
                final = (content or "").strip() or "Scanner online."
                return re.sub(r"<system-reminder>.*?</system-reminder>", "", final,
                              flags=re.DOTALL | re.IGNORECASE).strip() or "Scanner online."
            messages.append({"role": "assistant", "content": content or "",
                             "tool_calls": [{"id": tc["id"], "type": "function",
                                             "function": {"name": tc["name"],
                                                          "arguments": json.dumps(tc["arguments"])}} for tc in tool_calls]})
            for tc in tool_calls:
                res = self.tools.execute(tc["name"], tc["arguments"])
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "name": tc["name"], "content": res})
        try:
            content, _, _ = self.brain.chat(messages, tools=None)
            return content or "Done."
        except Exception as e:
            return f"Done (khataye payani: {e})"

    def confirm_signal(self, scan_report: str, symbol: str) -> str:
        """Second-opinion LLM ru ALIGNED e deterministik.
        Return: 'CONFIRM: <payam>' ya 'REJECT: <dalil>'."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Scan e deterministik ALIGNED dade baraye {symbol}. "
                f"Ba rules (RSI-first, min_agree, veto, forming-candle) check kon.\n"
                f"Gozaresh:\n{scan_report}\n\n"
                f"Javab ba 'CONFIRM:' ya 'REJECT:' shoru kon + 2-3 khat dalil (Finglish). "
                f"Trade baz nakon — faghat nazar + payam e alert e amade KCEX manual."
            )},
        ]
        try:
            content, _, _ = self.brain.chat(messages, tools=None)
            return (content or "").strip() or "CONFIRM: (javab khali)"
        except Exception as e:
            logger.warning(f"confirm failed, fallback CONFIRM: {e}")
            return "CONFIRM: (LLM error — deterministic ALIGNED ghabul)"
