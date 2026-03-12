import os
import logging
import hmac
import hashlib
import time
import requests
from datetime import datetime
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_CHAT_ID  = int(os.getenv("ALLOWED_CHAT_ID", "0"))
BASE_URL         = "https://open-api.bingx.com"

DEFAULT_SETTINGS = {
    "symbol":         "BTC-USDT",
    "trade_amount":   10.0,
    "rsi_oversold":   30,
    "rsi_overbought": 70,
}

class BotState:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self.position: Optional[dict] = None
        self.auto_trade: bool = False

state = BotState()

def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(BINGX_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def bingx_get(path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bingx_post(path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    r = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def get_price(symbol):
    r = requests.get(f"{BASE_URL}/openApi/spot/v1/ticker/price",
                     params={"symbol": symbol}, timeout=10)
    data = r.json()
    return float(data["data"]["price"])

def get_klines(symbol, interval="1h", limit=50):
    r = requests.get(f"{BASE_URL}/openApi/spot/v2/market/kline",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=10)
    data = r.json()
    closes = [float(k[4]) for k in data["data"]]
    return closes

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def get_market_data(symbol):
    closes = get_klines(symbol)
    price  = closes[-1]
    rsi    = calculate_rsi(closes)
    sma20  = round(sum(closes[-20:]) / 20, 2)
    sma50  = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else sma20
    change = round(((closes[-1] - closes[-24]) / closes[-24]) * 100, 2) if len(closes) >= 24 else 0
    return {
        "symbol": symbol, "price": round(price, 2),
        "change_24h": change, "rsi": rsi,
        "sma20": sma20, "sma50": sma50,
    }

def generate_signal(data):
    score = 0
    if data["rsi"] < state.settings["rsi_oversold"]:
        score += 2
    elif data["rsi"] > state.settings["rsi_overbought"]:
        score -= 2
    if data["sma20"] > data["sma50"]:
        score += 1
    else:
        score -= 1
    if score >= 2:
        return "🟢 AL (BUY)"
    elif score <= -2:
        return "🔴 SAT (SELL)"
    else:
        return "⚪ GÖZLƏYİN"

def get_balance():
    data = bingx_get("/openApi/spot/v1/account/balance")
    balances = []
    if "data" in data and "balances" in data["data"]:
        for b in data["data"]["balances"]:
            free = float(b.get("free", 0))
            if free > 0:
                balances.append(f"• *{b['asset']}*: {free:.4f}")
    return balances

def place_order(symbol, side, amount):
    params = {
        "symbol":    symbol,
        "side":      side,
        "type":      "MARKET",
        "quoteOrderQty": amount,
    }
    return bingx_post("/openApi/spot/v1/trade/order", params)

def auth_check(update: Update):
    return not (ALLOWED_CHAT_ID and update.effective_chat.id != ALLOWED_CHAT_ID)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    await update.message.reply_text(
        "🤖 *BingX Trading Bot*\n\n"
        "*/status* — Bazar vəziyyəti\n"
        "*/balance* — Hesab balansı\n"
        "*/buy BTC 10* — 10 USDT BTC al\n"
        "*/sell BTC 10* — 10 USDT BTC sat\n"
        "*/auto on* — Avto ticarəti aç\n"
        "*/auto off* — Avto ticarəti bağla\n"
        "*/settings* — Parametrlər\n"
        f"\n📌 Coin: *{state.settings['symbol']}*\n"
        f"💰 Məbləğ: *{state.settings['trade_amount']} USDT*",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ Məlumat yüklənir...")
    try:
        symbol = state.settings["symbol"]
        data   = get_market_data(symbol)
        signal = generate_signal(data)
        ch     = "📈" if data["change_24h"] >= 0 else "📉"
        pos_text = ""
        if state.position:
            p = state.position
            pnl = (data["price"] - p["entry_price"]) * p["qty"]
            pct = ((data["price"] - p["entry_price"]) / p["entry_price"]) * 100
            pos_text = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Açıq Mövqe*\n"
                f"Giriş: ${p['entry_price']:,.2f}\n"
                f"P&L: {'✅' if pnl>=0 else '❌'} ${pnl:+.2f} ({pct:+.2f}%)"
            )
        text = (
            f"📊 *{symbol}*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Qiymət: *${data['price']:,.2f}*\n"
            f"{ch} 24s: *{data['change_24h']:+.2f}%*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 RSI: *{data['rsi']}*\n"
            f"📈 SMA20: ${data['sma20']:,.2f}\n"
            f"📈 SMA50: ${data['sma50']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Siqnal: {signal}*"
            f"{pos_text}\n\n🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        keyboard = [[
            InlineKeyboardButton("🟢 AL", callback_data="quick_buy"),
            InlineKeyboardButton("🔴 SAT", callback_data="quick_sell"),
            InlineKeyboardButton("🔄 Yenilə", callback_data="refresh"),
        ]]
        await msg.edit_text(text, parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    try:
        balances = get_balance()
        if balances:
            text = "💼 *Hesab Balansı*\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(balances)
        else:
            text = "💼 Balans məlumatı tapılmadı"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Xəta: {e}")

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ İstifadə: /buy BTC 10")
        return
    symbol = args[0].upper() + "-USDT"
    amount = float(args[1])
    msg = await update.message.reply_text(f"⏳ {amount} USDT {symbol} alınır...")
    try:
        order = place_order(symbol, "BUY", amount)
        price = get_price(symbol)
        qty   = amount / price
        state.position = {"symbol": symbol, "entry_price": price, "qty": qty,
                          "time": datetime.now().isoformat()}
        await msg.edit_text(
            f"✅ *AL icra edildi!*\n🪙 {symbol} @ ${price:,.2f}\n💰 {amount} USDT",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ İstifadə: /sell BTC 10")
        return
    symbol = args[0].upper() + "-USDT"
    amount = float(args[1])
    msg = await update.message.reply_text(f"⏳ {amount} USDT {symbol} satılır...")
    try:
        order = place_order(symbol, "SELL", amount)
        price = get_price(symbol)
        qty   = amount / price
        pnl_text = ""
        if state.position and state.position["symbol"] == symbol:
            pnl = (price - state.position["entry_price"]) * qty
            pct = ((price - state.position["entry_price"]) / state.position["entry_price"]) * 100
            pnl_text = f"\n{'✅' if pnl>=0 else '❌'} P&L: ${pnl:+.2f} ({pct:+.2f}%)"
            state.position = None
        await msg.edit_text(
            f"✅ *SAT icra edildi!*\n🪙 {symbol} @ ${price:,.2f}{pnl_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if not args or args[0] not in ["on", "off"]:
        await update.message.reply_text("❗ /auto on  yaxud  /auto off")
        return
    state.auto_trade = (args[0] == "on")
    status = "🟢 *AKTİVDİR*" if state.auto_trade else "🔴 *DAYANDI*"
    await update.message.reply_text(f"Avtomatik ticarət {status}", parse_mode="Markdown")

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    s = state.settings
    await update.message.reply_text(
        f"⚙️ *Parametrlər*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"symbol: *{s['symbol']}*\n"
        f"trade_amount: *{s['trade_amount']} USDT*\n"
        f"rsi_oversold: *{s['rsi_oversold']}*\n"
        f"rsi_overbought: *{s['rsi_overbought']}*",
        parse_mode="Markdown"
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "refresh":
        await cmd_status(update, context)
    elif query.data == "quick_buy":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            place_order(symbol, "BUY", amount)
            price = get_price(symbol)
            qty   = amount / price
            state.position = {"symbol": symbol, "entry_price": price, "qty": qty,
                              "time": datetime.now().isoformat()}
            await query.message.reply_text(f"✅ AL: {symbol} @ ${price:,.2f}")
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")
    elif query.data == "quick_sell":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            place_order(symbol, "SELL", amount)
            price = get_price(symbol)
            state.position = None
            await query.message.reply_text(f"✅ SAT: {symbol} @ ${price:,.2f}")
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")

async def auto_trade_job(context: ContextTypes.DEFAULT_TYPE):
    if not state.auto_trade:
        return
    try:
        symbol = state.settings["symbol"]
        data   = get_market_data(symbol)
        signal = generate_signal(data)
        if "AL" in signal and state.position is None:
            amount = state.settings["trade_amount"]
            place_order(symbol, "BUY", amount)
            price = data["price"]
            qty   = amount / price
            state.position = {"symbol": symbol, "entry_price": price, "qty": qty,
                              "time": datetime.now().isoformat()}
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"🤖 OTO AL: {symbol} @ ${price:,.2f}\nRSI: {data['rsi']}"
            )
        elif "SAT" in signal and state.position is not None:
            amount = state.settings["trade_amount"]
            place_order(symbol, "SELL", amount)
            price = data["price"]
            pnl   = (price - state.position["entry_price"]) * (amount / price)
            state.position = None
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"🤖 OTO SAT: {symbol} @ ${price:,.2f}\nP&L: ${pnl:+.2f}"
            )
    except Exception as e:
        logger.error(f"Auto trade xətası: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("buy",      cmd_buy))
    app.add_handler(CommandHandler("sell",     cmd_sell))
    app.add_handler(CommandHandler("auto",     cmd_auto))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(auto_trade_job, interval=60, first=10)
    print("🤖 BingX Bot işə düşdü...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
