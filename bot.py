"""
Binance Trading Bot with Telegram Interface
============================================
Komandalar:
  /start    - Botu başlat
  /status   - Cari mövqe və P&L
  /balance  - Hesab balansı
  /buy      - Manual al (məs: /buy BTC 10)
  /sell     - Manual sat (məs: /sell BTC 10)
  /auto on  - Avtomatik ticarəti aç
  /auto off - Avtomatik ticarəti bağla
  /settings - Parametrləri göstər
  /set      - Parametr dəyiş (məs: /set stop_loss 2.5)
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, JobQueue
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─── KONFİQURASİYA ────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_CHAT_ID    = int(os.getenv("ALLOWED_CHAT_ID", "0"))  # Yalnız sənin ID-n

DEFAULT_SETTINGS = {
    "symbol":         "BTCUSDT",
    "trade_amount":   10.0,       # USDT
    "stop_loss":      2.0,        # %
    "take_profit":    4.0,        # %
    "rsi_period":     14,
    "rsi_oversold":   30,
    "rsi_overbought": 70,
    "check_interval": 60,         # saniyə
    "auto_trade":     False,
}

# ─── BOT VƏZİYYƏTİ ───────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self.position: Optional[dict] = None   # Açıq mövqe
        self.trade_history: list = []
        self.auto_trade: bool = False

state = BotState()

# ─── BİNANCE CLIENT ──────────────────────────────────────────────────────────
def get_client() -> Client:
    return Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# ─── TEXNİKİ ANALİZ ──────────────────────────────────────────────────────────
def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    rsi   = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

def calculate_macd(prices: pd.Series):
    ema12  = prices.ewm(span=12, adjust=False).mean()
    ema26  = prices.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    return round(macd.iloc[-1], 4), round(signal.iloc[-1], 4), round(hist.iloc[-1], 4)

def get_market_data(symbol: str, interval: str = "1h", limit: int = 100) -> dict:
    """Binance-dən kline (şam) məlumatları al və analiz et."""
    client = get_client()
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    
    closes = pd.Series([float(k[4]) for k in klines])
    highs  = pd.Series([float(k[2]) for k in klines])
    lows   = pd.Series([float(k[3]) for k in klines])
    
    current_price = closes.iloc[-1]
    rsi           = calculate_rsi(closes, state.settings["rsi_period"])
    macd, signal, hist = calculate_macd(closes)
    
    sma20 = round(closes.rolling(20).mean().iloc[-1], 2)
    sma50 = round(closes.rolling(50).mean().iloc[-1], 2)
    
    change_24h = round(((current_price - closes.iloc[-24]) / closes.iloc[-24]) * 100, 2)
    
    return {
        "symbol":       symbol,
        "price":        round(current_price, 2),
        "change_24h":   change_24h,
        "rsi":          rsi,
        "macd":         macd,
        "macd_signal":  signal,
        "macd_hist":    hist,
        "sma20":        sma20,
        "sma50":        sma50,
        "high_24h":     round(highs.iloc[-24:].max(), 2),
        "low_24h":      round(lows.iloc[-24:].min(), 2),
    }

def generate_signal(data: dict) -> str:
    """RSI + MACD + SMA əsasında AL/SAT/GÖZLƏYİN siqnalı."""
    score = 0
    
    # RSI
    if data["rsi"] < state.settings["rsi_oversold"]:
        score += 2    # Oversold → Al
    elif data["rsi"] > state.settings["rsi_overbought"]:
        score -= 2    # Overbought → Sat
    
    # MACD cross
    if data["macd"] > data["macd_signal"] and data["macd_hist"] > 0:
        score += 1
    elif data["macd"] < data["macd_signal"] and data["macd_hist"] < 0:
        score -= 1
    
    # SMA trend
    if data["sma20"] > data["sma50"]:
        score += 1    # Bullish trend
    else:
        score -= 1    # Bearish trend
    
    if score >= 3:
        return "🟢 AL (STRONG BUY)"
    elif score >= 1:
        return "🟡 AL (BUY)"
    elif score <= -3:
        return "🔴 SAT (STRONG SELL)"
    elif score <= -1:
        return "🟠 SAT (SELL)"
    else:
        return "⚪ GÖZLƏYİN (NEUTRAL)"

# ─── TİCARƏT FUNKSİYALARI ───────────────────────────────────────────────────
def get_balance(asset: str = "USDT") -> float:
    client = get_client()
    info   = client.get_asset_balance(asset=asset)
    return float(info["free"]) if info else 0.0

def place_market_order(symbol: str, side: str, usdt_amount: float) -> dict:
    """Market order aç. side: 'BUY' or 'SELL'"""
    client = get_client()
    
    if side == "BUY":
        order = client.order_market_buy(
            symbol=symbol,
            quoteOrderQty=usdt_amount  # USDT miqdarı ilə al
        )
    else:
        # SAT üçün coin miqdarını hesabla
        price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        qty   = usdt_amount / price
        info  = client.get_symbol_info(symbol)
        step  = float(next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")["stepSize"])
        qty   = round(qty - (qty % step), 8)
        order = client.order_market_sell(symbol=symbol, quantity=qty)
    
    return order

def set_stop_loss_take_profit(symbol: str, qty: float, entry_price: float, side: str):
    """OCO (One-Cancels-Other) order ilə SL/TP qur."""
    client = get_client()
    sl_pct = state.settings["stop_loss"] / 100
    tp_pct = state.settings["take_profit"] / 100
    
    if side == "BUY":
        stop_price  = round(entry_price * (1 - sl_pct), 2)
        limit_price = round(entry_price * (1 + tp_pct), 2)
    else:
        stop_price  = round(entry_price * (1 + sl_pct), 2)
        limit_price = round(entry_price * (1 - tp_pct), 2)
    
    try:
        client.order_oco_sell(
            symbol=symbol,
            quantity=qty,
            price=str(limit_price),
            stopPrice=str(stop_price),
            stopLimitPrice=str(round(stop_price * 0.999, 2)),
            stopLimitTimeInForce="GTC"
        )
        return stop_price, limit_price
    except BinanceAPIException as e:
        logger.error(f"OCO order xətası: {e}")
        return None, None

# ─── TELEGRAM KOMANDALAR ──────────────────────────────────────────────────────
def auth_check(update: Update) -> bool:
    """Yalnız icazəli istifadəçi əmr verə bilər."""
    if ALLOWED_CHAT_ID and update.effective_chat.id != ALLOWED_CHAT_ID:
        return False
    return True

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update):
        return
    
    text = (
        "🤖 *Binance Trading Bot*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*/status*   — Bazar vəziyyəti\n"
        "*/balance*  — Hesab balansı\n"
        "*/buy* BTC 10 — 10 USDT BTC al\n"
        "*/sell* BTC 10 — 10 USDT BTC sat\n"
        "*/auto on*  — Avto ticarəti aç\n"
        "*/auto off* — Avto ticarəti bağla\n"
        "*/settings* — Cari parametrlər\n"
        "*/set* stop_loss 2.5 — Parametr dəyiş\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Cari coin: *{state.settings['symbol']}*\n"
        f"💰 Ticarət miqdarı: *{state.settings['trade_amount']} USDT*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    
    msg = await update.message.reply_text("⏳ Məlumat yüklənir...")
    
    try:
        symbol = state.settings["symbol"]
        data   = get_market_data(symbol)
        signal = generate_signal(data)
        
        change_emoji = "📈" if data["change_24h"] >= 0 else "📉"
        macd_trend   = "↗️" if data["macd_hist"] > 0 else "↘️"
        
        position_text = ""
        if state.position:
            p    = state.position
            pnl  = (data["price"] - p["entry_price"]) * p["qty"]
            pnl_pct = ((data["price"] - p["entry_price"]) / p["entry_price"]) * 100
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            position_text = (
                f"\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Açıq Mövqe*\n"
                f"Giriş: ${p['entry_price']:,.2f}\n"
                f"Miqdar: {p['qty']} {symbol.replace('USDT','')}\n"
                f"P&L: {pnl_emoji} ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"SL: ${p.get('sl', 'N/A')} | TP: ${p.get('tp', 'N/A')}"
            )
        
        text = (
            f"📊 *{symbol} Analiz*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Qiymət: *${data['price']:,.2f}*\n"
            f"{change_emoji} 24s dəyişim: *{data['change_24h']:+.2f}%*\n"
            f"🔺 Yüksək: ${data['high_24h']:,.2f}\n"
            f"🔻 Aşağı: ${data['low_24h']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 RSI({state.settings['rsi_period']}): *{data['rsi']}*\n"
            f"📊 MACD: {data['macd']} {macd_trend}\n"
            f"   Signal: {data['macd_signal']}\n"
            f"📈 SMA20: ${data['sma20']:,.2f}\n"
            f"📈 SMA50: ${data['sma50']:,.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Siqnal: {signal}*"
            f"{position_text}\n\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🟢 AL", callback_data="quick_buy"),
                InlineKeyboardButton("🔴 SAT", callback_data="quick_sell"),
                InlineKeyboardButton("🔄 Yenilə", callback_data="refresh_status"),
            ]
        ]
        await msg.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    
    try:
        client  = get_client()
        account = client.get_account()
        
        balances = [
            b for b in account["balances"]
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        ]
        
        lines = ["💼 *Hesab Balansı*\n━━━━━━━━━━━━━━━━━━━━"]
        for b in balances[:15]:  # Max 15 coin göstər
            free   = float(b["free"])
            locked = float(b["locked"])
            if free + locked > 0:
                lock_text = f" 🔒{locked:.4f}" if locked > 0 else ""
                lines.append(f"• *{b['asset']}*: {free:.4f}{lock_text}")
        
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Xəta: {e}")

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❗ İstifadə: /buy BTC 10\n(10 USDT dəyərindəki BTC al)"
        )
        return
    
    coin   = args[0].upper() + "USDT"
    amount = float(args[1])
    
    msg = await update.message.reply_text(f"⏳ {coin} üçün {amount} USDT AL əmri...")
    
    try:
        order      = place_market_order(coin, "BUY", amount)
        exec_price = float(order["fills"][0]["price"])
        exec_qty   = float(order["executedQty"])
        
        # Stop-loss / Take-profit qur
        sl, tp = set_stop_loss_take_profit(coin, exec_qty, exec_price, "BUY")
        
        state.position = {
            "symbol":      coin,
            "side":        "BUY",
            "entry_price": exec_price,
            "qty":         exec_qty,
            "sl":          sl,
            "tp":          tp,
            "time":        datetime.now().isoformat()
        }
        state.trade_history.append(state.position.copy())
        
        sl_text = f"${sl:,.2f}" if sl else "N/A"
        tp_text = f"${tp:,.2f}" if tp else "N/A"
        
        await msg.edit_text(
            f"✅ *AL əmri icra edildi!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 Coin: *{coin}*\n"
            f"💵 Qiymət: *${exec_price:,.2f}*\n"
            f"📦 Miqdar: *{exec_qty}*\n"
            f"💰 Məbləğ: *{amount} USDT*\n"
            f"🛑 Stop-Loss: *{sl_text}*\n"
            f"🎯 Take-Profit: *{tp_text}*",
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
        await update.message.reply_text("❗ İstifadə: /sell BTC 10\n(10 USDT dəyərindəki BTC sat)")
        return
    
    coin   = args[0].upper() + "USDT"
    amount = float(args[1])
    
    msg = await update.message.reply_text(f"⏳ {coin} üçün {amount} USDT SAT əmri...")
    
    try:
        order      = place_market_order(coin, "SELL", amount)
        exec_price = float(order["fills"][0]["price"])
        exec_qty   = float(order["executedQty"])
        
        pnl_text = ""
        if state.position and state.position["symbol"] == coin:
            pnl     = (exec_price - state.position["entry_price"]) * exec_qty
            pnl_pct = ((exec_price - state.position["entry_price"]) / state.position["entry_price"]) * 100
            pnl_emoji = "✅" if pnl >= 0 else "❌"
            pnl_text  = f"\n{pnl_emoji} P&L: *${pnl:+.2f}* ({pnl_pct:+.2f}%)"
            state.position = None
        
        await msg.edit_text(
            f"✅ *SAT əmri icra edildi!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 Coin: *{coin}*\n"
            f"💵 Qiymət: *${exec_price:,.2f}*\n"
            f"📦 Miqdar: *{exec_qty}*"
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
        await update.message.reply_text("❗ İstifadə: /auto on  yaxud  /auto off")
        return
    
    state.auto_trade = (args[0] == "on")
    state.settings["auto_trade"] = state.auto_trade
    
    if state.auto_trade:
        await update.message.reply_text(
            "🟢 *Avtomatik ticarət AKTİVDİR*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Coin: {state.settings['symbol']}\n"
            f"💰 Hər ticarət: {state.settings['trade_amount']} USDT\n"
            f"🛑 Stop-Loss: {state.settings['stop_loss']}%\n"
            f"🎯 Take-Profit: {state.settings['take_profit']}%\n"
            f"⏱ Yoxlama intervalı: {state.settings['check_interval']}s",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("🔴 *Avtomatik ticarət DAYANDI*", parse_mode="Markdown")

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    
    s = state.settings
    text = (
        "⚙️ *Cari Parametrlər*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 symbol: *{s['symbol']}*\n"
        f"💰 trade_amount: *{s['trade_amount']} USDT*\n"
        f"🛑 stop_loss: *{s['stop_loss']}%*\n"
        f"🎯 take_profit: *{s['take_profit']}%*\n"
        f"📊 rsi_period: *{s['rsi_period']}*\n"
        f"📉 rsi_oversold: *{s['rsi_oversold']}*\n"
        f"📈 rsi_overbought: *{s['rsi_overbought']}*\n"
        f"⏱ check_interval: *{s['check_interval']}s*\n\n"
        "Dəyişdirmək üçün:\n"
        "`/set parametr dəyər`\n"
        "Məs: `/set stop_loss 3`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❗ İstifadə: /set parametr dəyər")
        return
    
    key = args[0].lower()
    val = args[1]
    
    if key not in state.settings:
        await update.message.reply_text(f"❌ Naməlum parametr: {key}")
        return
    
    try:
        if key == "symbol":
            state.settings[key] = val.upper()
        elif key == "auto_trade":
            state.settings[key] = val.lower() == "true"
        else:
            state.settings[key] = float(val)
        
        await update.message.reply_text(f"✅ *{key}* → *{state.settings[key]}*", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text(f"❌ Yanlış dəyər: {val}")

# ─── CALLBACK BUTTONS ─────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "refresh_status":
        # Status-u yenilə
        fake_update = update
        fake_update._effective_message = query.message
        await cmd_status(fake_update, context)
    
    elif query.data == "quick_buy":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            order      = place_market_order(symbol, "BUY", amount)
            exec_price = float(order["fills"][0]["price"])
            exec_qty   = float(order["executedQty"])
            sl, tp     = set_stop_loss_take_profit(symbol, exec_qty, exec_price, "BUY")
            state.position = {
                "symbol": symbol, "side": "BUY",
                "entry_price": exec_price, "qty": exec_qty,
                "sl": sl, "tp": tp, "time": datetime.now().isoformat()
            }
            await query.message.reply_text(
                f"✅ AL: {exec_qty} {symbol} @ ${exec_price:,.2f}\n"
                f"SL: ${sl} | TP: ${tp}"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")
    
    elif query.data == "quick_sell":
        symbol = state.settings["symbol"]
        amount = state.settings["trade_amount"]
        try:
            order      = place_market_order(symbol, "SELL", amount)
            exec_price = float(order["fills"][0]["price"])
            exec_qty   = float(order["executedQty"])
            await query.message.reply_text(
                f"✅ SAT: {exec_qty} {symbol} @ ${exec_price:,.2f}"
            )
            state.position = None
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")

# ─── OTOMATİK TİCARƏT (JOB) ──────────────────────────────────────────────────
async def auto_trade_job(context: ContextTypes.DEFAULT_TYPE):
    """Hər interval-da çalışır, siqnal yoxlayır."""
    if not state.auto_trade:
        return
    
    try:
        symbol = state.settings["symbol"]
        data   = get_market_data(symbol)
        signal = generate_signal(data)
        
        # AL siqnalı → mövqe yoxdursa al
        if "AL" in signal and state.position is None:
            amount = state.settings["trade_amount"]
            order  = place_market_order(symbol, "BUY", amount)
            ep     = float(order["fills"][0]["price"])
            qty    = float(order["executedQty"])
            sl, tp = set_stop_loss_take_profit(symbol, qty, ep, "BUY")
            state.position = {
                "symbol": symbol, "side": "BUY",
                "entry_price": ep, "qty": qty,
                "sl": sl, "tp": tp,
                "time": datetime.now().isoformat()
            }
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=(
                    f"🤖 *OTOMATİK AL*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 {symbol} @ ${ep:,.2f}\n"
                    f"📦 {qty}\n"
                    f"🛑 SL: ${sl} | 🎯 TP: ${tp}\n"
                    f"📊 RSI: {data['rsi']} | Siqnal: {signal}"
                ),
                parse_mode="Markdown"
            )
        
        # SAT siqnalı → açıq mövqe varsa sat
        elif "SAT" in signal and state.position is not None:
            amount = state.settings["trade_amount"]
            order  = place_market_order(symbol, "SELL", amount)
            ep     = float(order["fills"][0]["price"])
            qty    = float(order["executedQty"])
            pnl    = (ep - state.position["entry_price"]) * qty
            state.position = None
            
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=(
                    f"🤖 *OTOMATİK SAT*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 {symbol} @ ${ep:,.2f}\n"
                    f"📦 {qty}\n"
                    f"{'✅' if pnl >= 0 else '❌'} P&L: ${pnl:+.2f}\n"
                    f"📊 RSI: {data['rsi']} | Siqnal: {signal}"
                ),
                parse_mode="Markdown"
            )
    
    except Exception as e:
        logger.error(f"Auto trade xətası: {e}")
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=f"⚠️ Oto-ticarət xətası: {e}"
        )

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    if not all([BINANCE_API_KEY, BINANCE_API_SECRET, TELEGRAM_TOKEN]):
        print("❌ .env faylında API açarları çatışmır!")
        return
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Komandalar
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("buy",      cmd_buy))
    app.add_handler(CommandHandler("sell",     cmd_sell))
    app.add_handler(CommandHandler("auto",     cmd_auto))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("set",      cmd_set))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Avtomatik ticarət işi
    interval = state.settings["check_interval"]
    app.job_queue.run_repeating(auto_trade_job, interval=interval, first=10)
    
    print("🤖 Bot işə düşdü...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
