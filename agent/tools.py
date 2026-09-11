"""ScannerAgent tools — bedune HICH tool e trade.
Mesle XT vali faghat: status, scan, market data, set_symbol, set_setting, remember.
open_trade / close / balance / position — vojood NADARE (by design).
"""

import logging
import time

logger = logging.getLogger("scan_tools")

TOOLS = [
    {"name": "get_status",
     "description": "Vaziat scanner: symbol, settings, cooldowns, akharin alert.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "scan_market",
     "description": "Run technical scan (EMA/MACD/RSI/MOMENTUM, RSI-first, min_agree) ru symbol. Returns direction/confidence/report.",
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "optional, default current"}}}},
    {"name": "get_market_data",
     "description": "Price + akharin candle ha + source (binance/bybit) baraye symbol.",
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "e.g. BTCUSDT"},
         "interval": {"type": "string", "description": "1m,3m,5m,15m,1h,4h,1d"}}}},
    {"name": "set_symbol",
     "description": "Avaz symbol e scan (mesle BTC, saga_usdt).",
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "new symbol"}}}, "required": ["symbol"]},
    {"name": "set_setting",
     "description": "Tanzim: timeframes, min_confidence, tf_min_confidence, min_agreeing_strategies, cooldown_minutes. INTERVAL HA INJA NIST — report=set_report_interval, scan=set_scan_interval.",
     "parameters": {"type": "object", "properties": {
         "key": {"type": "string"}, "value": {"type": "string"}}}, "required": ["key", "value"]},
    {"name": "set_report_interval",
     "description": "Gozaresh KAMEL har N saniye be user, hatta bedune signal. Vaghti user mige 'report interval'/'send reports'/'show scans'/'bebinim chi mige' IN RA bezan. 0=off.",
     "parameters": {"type": "object", "properties": {
         "seconds": {"type": "string"}}}, "required": ["seconds"]},
    {"name": "set_scan_interval",
     "description": "Har chand saniye scanner dar background run shavad (SILENT, faghat ALIGNED). Vaghti user mige 'scan interval'/'scan frequency' IN RA bezan. Min 15.",
     "parameters": {"type": "object", "properties": {
         "seconds": {"type": "string"}}}, "required": ["seconds"]},
    {"name": "remember",
     "description": "Zakhire dars dar hafeze (observation, lesson).",
     "parameters": {"type": "object", "properties": {
         "key": {"type": "string"}, "value": {"type": "string"}}}, "required": ["key", "value"]},
    {"name": "no_signal",
     "description": "Sabt tasmim: alan signal nist, sabr. Dalil begu.",
     "parameters": {"type": "object", "properties": {
         "reason": {"type": "string"}}}, "required": ["reason"]},
]

TOOLS_AUTO = [t for t in TOOLS if t["name"] not in ("set_symbol", "set_setting", "set_report_interval", "set_scan_interval")]


class ScanTools:
    def __init__(self, scanner, client, settings: dict, save_fn=None):
        self.scanner = scanner
        self.client = client
        self.settings = settings
        self._save = save_fn or (lambda: None)
        self.notes = []  # remember shode ha (sade, tu RAM + settings.json age khasti)
        self.decisions = []

    def execute(self, name: str, args: dict, allow_settings: bool = True) -> str:
        if not allow_settings and name in ("set_symbol", "set_setting", "set_report_interval", "set_scan_interval"):
            return (f"Tool {name} dar autonomous mode بسته: symbol/settings faghat "
                    f"ba dastoor mostaghim user (chat) avaz mishe.")
        h = {"get_status": self._status, "scan_market": self._scan,
             "get_market_data": self._mdata, "set_symbol": self._symbol,
             "set_setting": self._setting, "remember": self._remember,
             "set_report_interval": self._report_iv, "set_scan_interval": self._scan_iv,
             "no_signal": self._no_signal}.get(name)
        if not h:
            return f"Unknown tool: {name}"
        try:
            return h(args or {})
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}", exc_info=True)
            return f"Tool {name} error: {e}"

    def _status(self, args: dict) -> str:
        from bot.market_client import normalize_symbol, to_display
        sym = normalize_symbol(self.settings.get("symbol", "BTCUSDT"))
        price = self.client.get_price(sym)
        return (f"Symbol: {to_display(sym)} ({self.client.last_source(sym)}) @ {price}\n"
                f"TF: {self.settings.get('timeframes')} | min {self.settings.get('min_confidence')}% "
                f"(tf {self.settings.get('tf_min_confidence')}%) | agree {self.settings.get('min_agreeing_strategies')}\n"
                f"Cooldown {self.settings.get('cooldown_minutes')}m | scan {self.settings.get('scan_interval_sec')}s\n"
                f"MODE: scanner, NO-TRADE — hich order zadه nemishe.")

    def _scan(self, args: dict) -> str:
        from bot.market_client import normalize_symbol
        sym = normalize_symbol(args.get("symbol") or self.settings.get("symbol", "BTCUSDT"))
        res = self.scanner.scan_multi_timeframe(sym)
        return self.scanner.format_report(res)

    def _mdata(self, args: dict) -> str:
        from bot.market_client import normalize_symbol
        sym = normalize_symbol(args.get("symbol") or self.settings.get("symbol", "BTCUSDT"))
        iv = args.get("interval") or "15m"
        kl = self.client.get_klines(sym, iv, limit=20) or []
        price = self.client.get_price(sym)
        out = f"{sym} ({self.client.last_source(sym)}) price: {price}\nLast {len(kl)} klines ({iv}):\n"
        for k in kl[-5:]:
            out += f"  {k}\n"
        return out

    def _symbol(self, args: dict) -> str:
        from bot.market_client import normalize_symbol, to_display
        sym = normalize_symbol(args.get("symbol", ""))
        ok, src, price = self.client.validate(sym)
        if not ok:
            return f"{args.get('symbol')} peyda nashod (Binance/Bybit)."
        self.settings["symbol"] = sym
        self._save()
        self.scanner.settings.update(self.settings)
        return f"Symbol: {to_display(sym)} ({src}) @ {price}"

    KEY_ALIASES = {
        "minconf": "min_confidence", "min_conf": "min_confidence",
        "tfmin": "tf_min_confidence", "tf_min": "tf_min_confidence",
        "agree": "min_agreeing_strategies", "min_agree": "min_agreeing_strategies",
        "min_agreeing": "min_agreeing_strategies",
        "cooldown": "cooldown_minutes",
        "interval": "scan_interval_sec", "scan_interval": "scan_interval_sec",
        "report": "report_interval_sec", "report_interval": "report_interval_sec",
        "tfs": "timeframes", "tf": "timeframes",
    }

    def _setting(self, args: dict) -> str:
        key, val = (args.get("key") or "").strip().lower(), (args.get("value") or "").strip()
        key = self.KEY_ALIASES.get(key, key)
        if key in ("scan_interval_sec", "report_interval_sec"):
            return (f"STOP: interval ha ba set_setting set NEMISHAN. "
                    f"Baraye report => set_report_interval, baraye scan => set_scan_interval bezan.")
        if key in ("min_confidence", "tf_min_confidence", "min_agreeing_strategies",
                   "cooldown_minutes"):
            self.settings[key] = int(float(val))
        elif key in ("timeframes", "symbol"):
            self.settings[key] = val
        else:
            return f"Unknown setting: {key} (timeframes, min_confidence, tf_min_confidence, min_agreeing_strategies, cooldown_minutes)"
        self._save()
        self.scanner.settings.update(self.settings)
        return f"{key} = {val}"

    def _report_iv(self, args: dict) -> str:
        try:
            v = max(0, int(float(args.get("seconds", 0))))
        except (TypeError, ValueError):
            return "seconds adad nist"
        self.settings["report_interval_sec"] = v
        self._save()
        self.scanner.settings.update(self.settings)
        return (f"report_interval_sec = {v} "
                f"({'har ' + str(v) + 's gozaresh KAMEL' if v else 'OFF, faghat ALIGNED'} | "
                f"scan_interval_sec = {self.settings.get('scan_interval_sec')})")

    def _scan_iv(self, args: dict) -> str:
        try:
            v = max(15, int(float(args.get("seconds", 60))))
        except (TypeError, ValueError):
            return "seconds adad nist"
        self.settings["scan_interval_sec"] = v
        self._save()
        self.scanner.settings.update(self.settings)
        return (f"scan_interval_sec = {v} (SILENT | "
                f"report_interval_sec = {self.settings.get('report_interval_sec')})")

    def _remember(self, args: dict) -> str:
        self.notes.append({"t": time.time(), "k": args.get("key"), "v": args.get("value")})
        return f"Yadداشت شد: {args.get('key')}"

    def _no_signal(self, args: dict) -> str:
        reason = args.get("reason", "")
        self.decisions.append({"t": time.time(), "d": "WAIT", "r": reason})
        return f"SABR recorded: {reason}"
