"""
app.py — Flask backend
PWA-dan gələn əmrləri qəbul edir, botu idarə edir.
"""

import asyncio
import threading
import logging
import os
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from config import Config
from modules.coinmarketcap import CoinMarketCapModule
from modules.bingx import BingXModule
from modules.news_risk import NewsRiskModule
from modules.strategy import StrategyModule
from modules.telegram_notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Flask")

app = Flask(__name__, static_folder="pwa", static_url_path="/")
CORS(app)

config   = Config()
cmc      = CoinMarketCapModule(config)
bingx    = BingXModule(config)
news     = NewsRiskModule(config)
strategy = StrategyModule(config, bingx)
tg       = TelegramNotifier(config)

bot_state = {
    "running":    False,
    "started_at": None,
    "cycle":      0,
    "trades":     [],
    "risk_flags": {},
    "last_cycle": None,
    "errors":     [],
}
_bot_thread   = None
_stop_event   = threading.Event()


def _run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_bot_async())
    loop.close()


async def _bot_async():
    bingx_symbols = await bingx.get_available_symbols()
    await tg.send("🤖 *Bot başladı!*")

    while not _stop_event.is_set():
        bot_state["cycle"] += 1
        cycle = bot_state["cycle"]
        logger.info(f"=== Cycle {cycle} ===")

        try:
            top_coins = cmc.get_top_coins(limit=config.CMC_TOP_N)
            tradable  = bingx.filter_tradable(top_coins, bingx_symbols)

            news.refresh()
            risk_flags = {coin: news.is_risky(coin) for coin in tradable}
            bot_state["risk_flags"]  = risk_flags
            bot_state["last_cycle"]  = datetime.utcnow().isoformat()

            for coin, risky in risk_flags.items():
                if risky:
                    msg = f"⚠️ *{coin}* mənfi xəbər — trade dayandırıldı!"
                    await tg.send(msg)

            for coin in tradable:
                if _stop_event.is_set():
                    break
                if risk_flags.get(coin):
                    continue

                symbol = f"{coin}-USDT"
                signal = await strategy.get_signal(symbol)

                if signal in ("BUY", "SELL"):
                    result = await bingx.place_order(symbol, signal, config.ORDER_USDT_SIZE)
                    if result:
                        trade = {
                            "time":    datetime.utcnow().isoformat(),
                            "symbol":  symbol,
                            "side":    signal,
                            "price":   result.get("price", "N/A"),
                            "qty":     result.get("qty",   "N/A"),
                            "orderId": result.get("orderId", "N/A"),
                        }
                        bot_state["trades"].insert(0, trade)
                        bot_state["trades"] = bot_state["trades"][:50]

                        icon = "✅" if signal == "BUY" else "🔴"
                        await tg.send(
                            f"{icon} *{signal}* `{symbol}`\n"
                            f"Qiymət: `{trade['price']}`\n"
                            f"Miqdar: `{trade['qty']}`"
                        )

        except Exception as e:
            err = {"time": datetime.utcnow().isoformat(), "error": str(e)}
            bot_state["errors"].insert(0, err)
            bot_state["errors"] = bot_state["errors"][:20]
            logger.error(f"Cycle error: {e}", exc_info=True)
            await tg.send(f"❌ *Xəta:* `{e}`")

        for _ in range(config.LOOP_INTERVAL_SEC):
            if _stop_event.is_set():
                break
            await asyncio.sleep(1)

    await tg.send("🛑 *Bot dayandırıldı.*")
    logger.info("Bot stopped.")


@app.route("/api/start", methods=["POST"])
def start_bot():
    global _bot_thread
    if bot_state["running"]:
        return jsonify({"status": "already_running"}), 200
    _stop_event.clear()
    bot_state["running"]    = True
    bot_state["started_at"] = datetime.utcnow().isoformat()
    bot_state["cycle"]      = 0
    bot_state["trades"]     = []
    bot_state["errors"]     = []
    _bot_thread = threading.Thread(target=_run_loop, daemon=True)
    _bot_thread.start()
    return jsonify({"status": "started"}), 200


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    if not bot_state["running"]:
        return jsonify({"status": "not_running"}), 200
    _stop_event.set()
    bot_state["running"] = False
    return jsonify({"status": "stopped"}), 200


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({
        "running":    bot_state["running"],
        "started_at": bot_state["started_at"],
        "cycle":      bot_state["cycle"],
        "last_cycle": bot_state["last_cycle"],
        "risk_flags": bot_state["risk_flags"],
        "trade_count": len(bot_state["trades"]),
        "recent_errors": bot_state["errors"][:5],
    }), 200


@app.route("/api/trades", methods=["GET"])
def get_trades():
    return jsonify({"trades": bot_state["trades"]}), 200


@app.route("/api/risk", methods=["GET"])
def get_risk():
    news.refresh()
    return jsonify({
        "risk_flags": bot_state["risk_flags"],
        "summary":    news.get_summary(),
    }), 200


@app.route("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
