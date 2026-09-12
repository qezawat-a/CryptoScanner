"""CryptoScanner — scanner e sarasari (Binance/Bybit), bedune trade, bedune XT.

- Tak symbol dar har lahze (ba /symbol avaz mishe, mesle XT)
- Haman strategy/logic e XT (EMA/MACD/RSI/MOMENTUM + RSI-first + min_agree + veto)
- Faghat vaghti ALIGNED (direction + conf >= min) Telegram mide → khodet tu KCEX baz mikoni
- Hich order, hich API key e sarafi lazem nist

Run:  python3 scanner.py
Env:  TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, SCAN_SYMBOL (optional)
"""

import json
import logging
import os
import sys
import threading
import time

import requests

import config
from bot.market_client import MarketClient, normalize_symbol, to_display
from bot.signal_scanner import SignalScanner

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("scanner_main")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

settings = dict(config.DEFAULTS)
if os.path.exists(SETTINGS_PATH):
    try:
        with open(SETTINGS_PATH) as f:
            settings.update(json.load(f))
    except Exception as e:
        logger.warning(f"settings.json khande nashod: {e}")


INT_SETTINGS = ("min_confidence", "tf_min_confidence", "min_agreeing_strategies",
                "cooldown_minutes", "scan_interval_sec", "report_interval_sec")

# ---------- database (Neon, fallback SQLite local) ----------
db = None
try:
    from bot.db import DB
    db = DB()
    if db.has_any_setting():
        for k, v in db.all_settings().items():
            settings[k] = int(v) if k in INT_SETTINGS else v
        logger.info("Settings loaded from DB.")
    else:
        for k, v in settings.items():
            db.set_setting(k, v)
        logger.info("Settings seeded to DB.")
except Exception as e:
    logger.warning(f"DB OFF (file-only mode): {e}")
    db = None


def save_settings():
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        logger.warning(f"save settings failed: {e}")
    if db:
        try:
            for k, v in settings.items():
                db.set_setting(k, v)
        except Exception as e:
            logger.warning(f"DB save failed: {e}")


client = MarketClient()
scanner = SignalScanner(client, settings)

# ---------- agent (LLM second-opinion + chat, NO-TRADE) ----------
agent = None
try:
    from agent.brain import Brain
    from agent.core import ScannerAgent
    from agent.tools import ScanTools
    _tools = ScanTools(scanner, client, settings, save_fn=lambda: save_settings())
    _brain = Brain()
    agent = ScannerAgent(_tools, _brain, settings)
    logger.info(f"Agent brain ready: {agent.get_model_info()}")
except Exception as e:
    logger.warning(f"Agent brain OFF (deterministic only): {e}")
    agent = None

cooldowns = {}  # (symbol, side) -> timestamp
last_alert = {}  # symbol -> (direction, timestamp)
last_report = 0.0
stop = threading.Event()


# ---------- telegram ----------

def tg_send(text: str):
    token = config.TELEGRAM_BOT_TOKEN
    uid = config.TELEGRAM_USER_ID
    if not token or not uid:
        logger.info(f"[NO-TELEGRAM] {text[:300]}")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": uid, "text": text}, timeout=15)
    except Exception as e:
        logger.warning(f"telegram send failed: {e}")


def cmd_status() -> str:
    sym = normalize_symbol(settings.get("symbol", "BTCUSDT"))
    price = client.get_price(sym)
    brain = agent.get_model_info() if agent else "OFF (deterministic)"
    return (f"Scanner ON (no-trade)\n"
            f"Symbol: {to_display(sym)} ({client.last_source(sym)})\n"
            f"Price: {price}\n"
            f"TF: {settings.get('timeframes')} | min {settings.get('min_confidence')}% "
            f"(tf {settings.get('tf_min_confidence')}%) | agree {settings.get('min_agreeing_strategies')}\n"
            f"Cooldown: {settings.get('cooldown_minutes')}m | scan har {settings.get('scan_interval_sec')}s\n"
            f"Brain: {brain}")


def handle_command(text: str) -> str:
    parts = (text or "").strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower().lstrip("/")
    arg = " ".join(parts[1:]).strip()

    if cmd in ("start", "help"):
        return ("Scanner (no-trade) — haman strategy XT + LLM\n"
                "/symbol BTC (ya saga_usdt) — avaz symbol\n"
                "/scan — scan fe'li\n"
                "/history — signal haye sabt shode\n"
                "/status — vaziat\n"
                "/settings — didan hame tanzimat\n"
                "/agent <soal> — harf ba agent (tahlil, chera ALIGNED نشد؟)\n"
                "/timeframes 1m,5m,15m,1h\n"
                "/minconf 80 | /tfmin 70 | /agree 2\n"
                "/cooldown 15 (daghighe) | /interval 60 (sanie)\n"
                "/report 60 — ersal gozaresh kamel har 60s (0 = faghat ALIGNED)\n"
                "Ya mostaghim chat kon — agent javab mide.")
    if cmd == "symbol" and arg:
        sym = normalize_symbol(arg)
        ok, src, price = client.validate(sym)
        if not ok:
            return f"{arg} tu hich perp market nist (Binance/Bybit/MEXC/Gate/OKX/Bitget/BingX). Mesal: BTC, saga_usdt, brew_usdt"
        settings["symbol"] = sym
        save_settings()
        scanner.settings.update(settings)
        return f"Symbol: {to_display(sym)} ({src}) @ {price}"
    if cmd == "scan":
        sym = normalize_symbol(settings.get("symbol", "BTCUSDT"))
        res = scanner.scan_multi_timeframe(sym)
        return scanner.format_report(res)
    if cmd == "history":
        if not db:
            return "DB OFF — history nist."
        rows = db.recent_signals(8)
        if not rows:
            return "Hich signal sabt nashode."
        import datetime
        lines = ["=== Signal history ==="]
        for r in rows:
            t = datetime.datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
            flag = "❌REJECT " if r["rejected"] else "🚨"
            lines.append(f"{flag}{t} {r['symbol']} {r['direction']} {r['confidence']}% @ {r['price']} ({r['source']})")
        return "\n".join(lines)
    if cmd == "status":
        return cmd_status()
    if cmd == "settings":
        lines = ["=== Settings (mesle XT, ba chat avaz mishe) ==="]
        for k in ("symbol", "timeframes", "min_confidence", "tf_min_confidence",
                  "min_agreeing_strategies", "cooldown_minutes",
                  "scan_interval_sec", "report_interval_sec", "alignment_mode"):
            lines.append(f"{k} = {settings.get(k)}")
        lines.append("Mesal: /symbol storj_usdt | /minconf 60 | /agree 2 | /align loose")
        return "\n".join(lines)
    if cmd == "timeframes" and arg:
        settings["timeframes"] = arg
        save_settings()
        scanner.settings.update(settings)
        return f"Timeframes: {arg}"
    if cmd in ("minconf", "min_confidence") and arg:
        settings["min_confidence"] = int(arg)
        save_settings()
        scanner.settings.update(settings)
        return f"min_confidence: {arg}%"
    if cmd in ("tfmin", "tf_min_confidence") and arg:
        settings["tf_min_confidence"] = int(arg)
        save_settings()
        scanner.settings.update(settings)
        return f"tf_min_confidence: {arg}%"
    if cmd in ("agree", "min_agree") and arg:
        settings["min_agreeing_strategies"] = int(arg)
        save_settings()
        scanner.settings.update(settings)
        return f"min_agree: {arg}"
    if cmd == "cooldown" and arg:
        settings["cooldown_minutes"] = int(arg)
        save_settings()
        scanner.settings.update(settings)
        return f"cooldown: {arg}m"
    if cmd == "interval" and arg:
        settings["scan_interval_sec"] = max(15, int(arg))
        save_settings()
        scanner.settings.update(settings)
        return f"scan interval: {settings['scan_interval_sec']}s"
    if cmd == "report" and arg:
        settings["report_interval_sec"] = max(0, int(arg))
        save_settings()
        scanner.settings.update(settings)
        v = settings["report_interval_sec"]
        return f"report: {'har ' + str(v) + 's gozaresh kamel' if v else 'OFF (faghat ALIGNED)'}"
    if cmd == "align" and arg:
        v = arg.strip().lower()
        if v not in ("strict", "loose"):
            return "align: strict (mesle XT) ya loose (TF ham-jahat nesf vazn)"
        settings["alignment_mode"] = v
        save_settings()
        scanner.settings.update(settings)
        return f"alignment: {v}"
    if cmd == "agent" and arg:
        if not agent:
            return "Brain OFF — AI_API_KEY ro tu .env bezar."
        return agent.chat(arg)
    return ""


def tg_set_commands():
    """Menu e command ha tu Telegram (/) — mesle mini-app list."""
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        return
    cmds = [
        ("scan", "Scan fe'li symbol"),
        ("history", "Signal haye sabt shode"),
        ("symbol", "Avaz symbol (mesal /symbol saga_usdt)"),
        ("status", "Vaziat scanner"),
        ("settings", "Didan hame tanzimat"),
        ("agent", "Harf ba agent (mesal /agent chera sabr?)"),
        ("timeframes", "Set timeframes"),
        ("minconf", "Set min confidence"),
        ("tfmin", "Set timeframe min confidence"),
        ("agree", "Set min agreeing strategies"),
        ("cooldown", "Set cooldown (daghighe)"),
        ("interval", "Set scan interval (sanie)"),
        ("report", "Gozaresh kamel periodic (0=off)"),
        ("align", "strict=XT / loose=pump-friendly"),
    ]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                      json={"commands": [{"command": c, "description": d} for c, d in cmds]},
                      timeout=15)
        logger.info("Telegram command menu set.")
    except Exception as e:
        logger.warning(f"setMyCommands failed: {e}")


def telegram_poll():
    token = config.TELEGRAM_BOT_TOKEN
    uid = str(config.TELEGRAM_USER_ID or "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN nist — faghat log.")
        return
    offset = 0
    while not stop.is_set():
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=40).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text", "")
                if uid and chat != uid:
                    continue
                if text.startswith("/"):
                    reply = handle_command(text)
                    if reply:
                        tg_send(reply)
                elif text.strip() and agent:
                    try:
                        tg_send(agent.chat(text.strip()))
                    except Exception as e:
                        logger.warning(f"agent chat failed: {e}")
        except Exception as e:
            logger.warning(f"poll error: {e}")
            time.sleep(5)


# ---------- scan loop ----------

def scan_loop():
    global last_report
    while not stop.is_set():
        try:
            sym = normalize_symbol(settings.get("symbol", "BTCUSDT"))
            res = scanner.scan_multi_timeframe(sym)
            now = time.time()

            if scanner.is_aligned(res):
                direction = res["direction"]
                cd_min = int(settings.get("cooldown_minutes", 15))
                cd_key = (sym, direction)
                if now - cooldowns.get(cd_key, 0) >= cd_min * 60:
    prev = last_alert.get(sym)
    dup_window = max(30, cd_min * 30)  # dynamic: half of cooldown, min 30s
    if not (prev and prev[0] == direction and now - prev[1] < dup_window):

                        report = scanner.format_report(res)
                        verdict = ""
                        if agent and config.Config.AGENT_LLM_CONFIRM == "true":
                            try:
                                verdict = agent.confirm_signal(report, sym)
                                logger.info(f"LLM verdict: {verdict[:200]}")
                            except Exception as e:
                                verdict = f"REJECT: LLM error, safety reject — {e}"

                            if verdict.strip().upper().startswith("REJECT"):
                                logger.info(f"ALIGNED rejected by LLM {sym} {direction}")
                                if db:
                                    db.log_signal(sym, direction, res["confidence"],
                                                  res.get("signal_strength", 0), res.get("price", 0),
                                                  res.get("source", ""), verdict, rejected=True)
                                cooldowns[cd_key] = now
                                continue
                        msg = "🚨 " + report
                        if verdict:
                            msg += f"\n\n🧠 {verdict}"
                        msg += f"\nKCEX manual: {direction} {to_display(sym)} @ ~{res['price']}"
                        tg_send(msg)
                        if db:
                            db.log_signal(sym, direction, res["confidence"],
                                          res.get("signal_strength", 0), res.get("price", 0),
                                          res.get("source", ""), verdict)
                        cooldowns[cd_key] = now
                        last_alert[sym] = (direction, now)
                        logger.info(f"ALIGNED {sym} {direction} {res['confidence']}% -> alert sent")
                    else:
                        logger.info(f"ALIGNED duplicate skipped {sym} {direction}")
                else:
                    logger.info(f"ALIGNED cooldown {sym} {direction}")
            else:
                logger.info(f"{sym}: {res['direction']} {res['confidence']}% (no alert)")

            rep_iv = int(settings.get("report_interval_sec", 0) or 0)
            if rep_iv > 0 and now - last_report >= rep_iv:
                last_report = now
                tg_send("📊 " + scanner.format_report(res))
        except Exception as e:
            logger.error(f"scan error: {e}", exc_info=True)
        stop.wait(max(15, int(settings.get("scan_interval_sec", 60))))


def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_USER_ID:
        logger.warning("TELEGRAM_BOT_TOKEN/USER_ID nist — alert ha faghat tu log mian.")
        logger.warning("Behtar bot JADID besazi (bot e XT focusedه, 2 polling conflict mikonan).")
    sym = normalize_symbol(settings.get("symbol", "BTCUSDT"))
    ok, src, price = client.validate(sym)
    logger.info(f"Scanner start: {to_display(sym)} ok={ok} {src} @ {price}")
    logger.info(cmd_status())
    tg_send("✅ Scanner روشن شد (no-trade)\n" + cmd_status())
    tg_set_commands()

    t1 = threading.Thread(target=telegram_poll, daemon=True)
    t1.start()
    try:
        scan_loop()
    except KeyboardInterrupt:
        logger.info("stop...")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
