"""
Binance Trading Bot with Telegram Interface
Komandalar:
  /start    - Botu başlat
  /status   - Bazar vəziyyəti və siqnal
  /balance  - Hesab balansı
  /buy BTC 10  - 10 USDT BTC al
  /sell BTC 10 - 10 USDT BTC sat
  /auto on  - Avtomatik ticarəti aç
  /auto off - Avtomatik ticarəti bağla
  /settings - Parametrləri göstər
  /set parametr dəyər - Parametr dəyiş
"""

import os
import logging
from datetime import datetime
from typing import Optional
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_CHAT_ID    = int(os.getenv("ALLOWED_CHAT_ID", "0"))

DEFAULT_SETTINGS = {
    "symbol":         "BTCUSDT",
    "trade_amount":   10.0,
    "stop_loss":      2.0,
    "take_profit":    4.0,
    "rsi_oversold":   30,
    "rsi_overbought": 70,
    "check_interval": 60,
    "auto_trade":     False,
}

class BotState:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self.position: Optional[dict] = None
        self.auto_trade: bool = False

state = BotState()

def get_client():
    return Client(BINANCE_API_KEY, BINANCE_API_SECRET)

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
    client = get_client()
    klines = client.get_klines(symbol=symbol, interval="1h", limit=50)
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    price  = closes[-1]
    rsi    = calculate_rsi(closes)
    change = round(((closes[-1] - closes[-24]) / closes[-24]) * 100, 2)
    sma20  = round(sum(closes[-20:]) / 20, 2)
    sma50  = round(sum(closes[-50:]) / 50, 2)
    return {
        "symbol":     symbol,
        "price":      round(price, 2),
        "change_24h": change,
        "rsi":        rsi,
        "sma20":      sma20,
        "sma50":      sma50,
        "high_24h":   round(max(highs[-24:]), 2),
        "low_24h":    round(min(lows[-24:]), 2),
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

def auth_check(update: Update):
    return not (ALLOWED_CHAT_ID and update.effective_chat.id != ALLOWED_CHAT_ID)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    text = (
        "🤖 *Binance Trading Bot*\n\n"
        "*/status* — Bazar vəziyyəti\n"
        "*/balance* — Hesab balansı\n"
        "*/buy BTC 10* — 10 USDT BTC al\n"
        "*/sell BTC 10* — 10 USDT BTC sat\n"
        "*/auto on* — Avto ticarəti aç\n"
        "*/auto off* — Avto ticarəti bağla\n"
        "*/settings* — Parametrlər\n"
        f"\n📌 Coin: *{state.settings['symbol']}*\n"
        f"💰 Məbləğ: *{state.settings['trade_amount']} USDT*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ Məlumat yüklənir...")
    try:
        symbol = state.settings["symbol"]
        data   = get_market_data(symbol)
        signal = generate_signal(data)
        ch_emoji = "📈" if data["change_24h"] >= 0 else "📉"
        pos_text = ""
        if state.position:
            p = state.position
            pnl = (data["price"] - p["entry_price"]) * p["qty"]
            pnl_pct = ((data["price"] - p["entry_price"]) / p["entry_price"]) * 100
            pos_text = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Açıq Mövqe*\n"
                f"Giriş: ${p['entry_price']:,.2f}\n"
                f"Miqdar: {p['qty']}\n"
                f"P&L: {'✅' if pnl>=0 else '❌'} ${pnl:+.2f} ({pnl_pct:+.2f}%)"
            )
        text = (
            f"📊 *{symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Qiymət: *${data['price']:,.2f}*\n"
            f"{ch_emoji} 24s: *{data['change_24h']:+.2f}%*\n"
            f"🔺 Yüksək: ${data['high_24h']:,.2f}\n"
            f"🔻 Aşağı: ${data['low_24h']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 RSI: *{data['rsi']}*\n"
            f"📈 SMA20: ${data['sma20']:,.2f}\n"
            f"📈 SMA50: ${data['sma50']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Siqnal: {signal}*"
            f"{pos_text}\n\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
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
        client  = get_client()
        account = client.get_account()
        balances = [b for b in account["balances"]
                    if float(b["free"]) > 0 or float(b["locked"]) > 0]
        lines = ["💼 *Hesab Balansı*\n━━━━━━━━━━━━━━━━━━━━"]
        for b in balances[:15]:
            free   = float(b["free"])
            locked = float(b["locked"])
            lock_t = f" 🔒{locked:.4f}" if locked > 0 else ""
            lines.append(f"• *{b['asset']}*: {free:.4f}{lock_t}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Xəta: {e}")

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ İstifadə: /buy BTC 10")
        return
    coin   = args[0].upper() + "USDT"
    amount = float(args[1])
    msg    = await update.message.reply_text(f"⏳ {amount} USDT {coin} alınır...")
    try:
        client = get_client()
        order  = client.order_market_buy(symbol=coin, quoteOrderQty=amount)
        ep     = float(order["fills"][0]["price"])
        qty    = float(order["executedQty"])
        state.position = {
            "symbol": coin, "entry_price": ep,
            "qty": qty, "time": datetime.now().isoformat()
        }
        await msg.edit_text(
            f"✅ *AL icra edildi!*\n"
            f"🪙 {coin} @ ${ep:,.2f}\n"
            f"📦 Miqdar: {qty}\n"
            f"💰 Məbləğ: {amount} USDT",
            parse_mode="Markdown"
        )
    except BinanceAPIException as e:
        await msg.edit_text(f"❌ Binance xətası: {e.message}")
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ İstifadə: /sell BTC 10")
        return
    coin   = args[0].upper() + "USDT"
    amount = float(args[1])
    msg    = await update.message.reply_text(f"⏳ {amount} USDT {coin} satılır...")
    try:
        client = get_client()
        price  = float(client.get_symbol_ticker(symbol=coin)["price"])
        info   = client.get_symbol_info(coin)
        step   = float(next(f for f in info["filters"]
                            if f["filterType"] == "LOT_SIZE")["stepSize"])
        qty    = amount / price
        qty    = round(qty - (qty % step), 8)
        order  = client.order_market_sell(symbol=coin, quantity=qty)
        ep     = float(order["fills"][0]["price"])
        pnl_text = ""
        if state.position and state.position["symbol"] == coin:
            pnl     = (ep - state.position["entry_price"]) * qty
            pnl_pct = ((ep - state.position["entry_price"]) / state.position["entry_price"]) * 100
            pnl_text = f"\n{'✅' if pnl>=0 else '❌'} P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)"
            state.position = None
        await msg.edit_text(
            f"✅ *SAT icra edildi!*\n"
            f"🪙 {coin} @ ${ep:,.2f}\n"
            f"📦 Miqdar: {qty}"
            f"{pnl_text}",
            parse_mode="Markdown"
        )
    except BinanceAPIException as e:
        await msg.edit_text(f"❌ Binance xətası: {e.message}")
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if not args or args[0] not in ["on", "off"]:
        await update.message.reply_text("❗ /auto on  yaxud  /auto off")
        return
    state.auto_trade = (args[0] == "on")
    if state.auto_trade:
        await update.message.reply_text(
            f"🟢 *Avtomatik ticarət AKTİVDİR*\n"
            f"📌 {state.settings['symbol']} | "
            f"💰 {state.settings['trade_amount']} USDT",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("🔴 *Avtomatik ticarət DAYANDI*", parse_mode="Markdown")

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    s = state.settings
    text = (
        "⚙️ *Parametrlər*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"symbol: *{s['symbol']}*\n"
        f"trade_amount: *{s['trade_amount']} USDT*\n"
        f"stop_loss: *{s['stop_loss']}%*\n"
        f"take_profit: *{s['take_profit']}%*\n"
        f"rsi_oversold: *{s['rsi_oversold']}*\n"
        f"rsi_overbought: *{s['rsi_overbought']}*\n\n"
        "Dəyişmək: `/set parametr dəyər`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ /set parametr dəyər")
        return
    key, val = args[0].lower(), args[1]
    if key not in state.settings:
        await update.message.reply_text(f"❌ Naməlum: {key}")
        return
    try:
        state.settings[key] = val.upper() if key == "symbol" else float(val)
        await update.message.reply_text(f"✅ *{key}* = *{state.settings[key]}*",
                                        parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text(f"❌ Yanlış dəyər: {val}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "refresh":
        await cmd_status(update, context)
    elif query.data == "quick_buy":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            client = get_client()
            order  = client.order_market_buy(symbol=symbol, quoteOrderQty=amount)
            ep     = float(order["fills"][0]["price"])
            qty    = float(order["executedQty"])
            state.position = {"symbol": symbol, "entry_price": ep, "qty": qty,
                              "time": datetime.now().isoformat()}
            await query.message.reply_text(f"✅ AL: {qty} {symbol} @ ${ep:,.2f}")
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")
    elif query.data == "quick_sell":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            client = get_client()
            price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
            info   = client.get_symbol_info(symbol)
            step   = float(next(f for f in info["filters"]
                                if f["filterType"] == "LOT_SIZE")["stepSize"])
            qty    = amount / price
            qty    = round(qty - (qty % step), 8)
            order  = client.order_market_sell(symbol=symbol, quantity=qty)
            ep     = float(order["fills"][0]["price"])
            state.position = None
            await query.message.reply_text(f"✅ SAT: {qty} {symbol} @ ${ep:,.2f}")
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
            client = get_client()
            order  = client.order_market_buy(symbol=symbol, quoteOrderQty=amount)
            ep     = float(order["fills"][0]["price"])
            qty    = float(order["executedQty"])
            state.position = {"symbol": symbol, "entry_price": ep, "qty": qty,
                              "time": datetime.now().isoformat()}
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"🤖 OTO AL: {qty} {symbol} @ ${ep:,.2f}\nRSI: {data['rsi']}"
            )
        elif "SAT" in signal and state.position is not None:
            amount = state.settings["trade_amount"]
            client = get_client()
            price  = float(client.get_symbol_ticker(symbol=symbol)["price"])
            info   = client.get_symbol_info(symbol)
            step   = float(next(f for f in info["filters"]
                                if f["filterType"] == "LOT_SIZE")["stepSize"])
            qty    = amount / price
            qty    = round(qty - (qty % step), 8)
            order  = client.order_market_sell(symbol=symbol, quantity=qty)
            ep     = float(order["fills"][0]["price"])
            pnl    = (ep - state.position["entry_price"]) * qty
            state.position = None
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"🤖 OTO SAT: {qty} {symbol} @ ${ep:,.2f}\nP&L: ${pnl:+.2f}"
            )
    except Exception as e:
        logger.error(f"Auto trade xətası: {e}")

def main():
    if not all([BINANCE_API_KEY, BINANCE_API_SECRET, TELEGRAM_TOKEN]):
        print("❌ .env faylında API açarları çatışmır!")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("buy",      cmd_buy))
    app.add_handler(CommandHandler("sell",     cmd_sell))
    app.add_handler(CommandHandler("auto",     cmd_auto))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("set",      cmd_set))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(auto_trade_job, interval=60, first=10)
    print("🤖 Bot işə düşdü...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
