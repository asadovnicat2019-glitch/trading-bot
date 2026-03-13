"""
BingX Ultimate Stable Bot v3
=============================
Rejim 1 — Smart Trend: 8 coini analiz edir, ən güclü siqnalı alır
Rejim 2 — Triangular Arbitraj: BingX daxilində üçbucaq arbitrajı
Düzəldildi: post_init crash, hmac xətası, API timeout-lar
"""

import os
import logging
import hmac
import hashlib
import time
import asyncio
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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

WATCHLIST = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT",
    "BNB-USDT", "DOGE-USDT", "AVAX-USDT", "MATIC-USDT",
]

# USDT → A → B → USDT üçbucaqları
TRIANGLES = [
    ("BTC-USDT", "ETH-BTC",   "ETH-USDT"),
    ("BTC-USDT", "BNB-BTC",   "BNB-USDT"),
    ("ETH-USDT", "SOL-ETH",   "SOL-USDT"),
    ("BTC-USDT", "XRP-BTC",   "XRP-USDT"),
    ("ETH-USDT", "MATIC-ETH", "MATIC-USDT"),
]

SETTINGS = {
    "trade_amount":   10.0,
    "rsi_oversold":   30,
    "rsi_overbought": 70,
    "min_score":      3,
    "arb_min_profit": 0.3,
    "trend_auto":     False,
    "arb_auto":       False,
}

current_position = None
arb_stats = {"total_profit": 0.0, "count": 0}

BOT_COMMANDS = [
    BotCommand("start",     "Botu başlat və menyu göstər"),
    BotCommand("scan",      "8 coini analiz et"),
    BotCommand("best",      "En yaxshi furset"),
    BotCommand("status",    "Cari aciq movqe"),
    BotCommand("balance",   "Hesab balansi"),
    BotCommand("trend",     "Trend ticareti - /trend on ya off"),
    BotCommand("arb",       "Arbitraj toggle - /arb on ya off"),
    BotCommand("arbscan",   "Arbitraj imkanlarini skan et"),
    BotCommand("arbstats",  "Arbitraj qazanc statistikasi"),
    BotCommand("watchlist", "Izlenen coinler siyahisi"),
    BotCommand("add",       "Coin elave et - /add DOGE"),
    BotCommand("remove",    "Coini sil - /remove DOGE"),
]

# ─── API ──────────────────────────────────────────────────────────────────────
def sign(params: dict) -> str:
    q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        BINGX_API_SECRET.encode("utf-8"),
        q.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def safe_get(url, params=None, timeout=10):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json()
    except Exception as e:
        logger.error(f"GET xətası {url}: {e}")
        return {}

def safe_post(url, params=None, headers=None, timeout=10):
    try:
        r = requests.post(url, params=params, headers=headers, timeout=timeout)
        return r.json()
    except Exception as e:
        logger.error(f"POST xətası {url}: {e}")
        return {}

def api_get(path, params=None):
    if not params: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    return safe_get(BASE_URL + path, params=params,
                    headers={"X-BX-APIKEY": BINGX_API_KEY})

def api_post(path, params=None):
    if not params: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    return safe_post(BASE_URL + path, params=params,
                     headers={"X-BX-APIKEY": BINGX_API_KEY})

def get_price(symbol) -> float:
    try:
        data = safe_get(f"{BASE_URL}/openApi/spot/v1/ticker/price",
                        params={"symbol": symbol})
        return float(data["data"]["price"])
    except Exception:
        return 0.0

def get_orderbook(symbol):
    """Bid/Ask — fallback to price if depth unavailable."""
    try:
        data = safe_get(f"{BASE_URL}/openApi/spot/v1/market/depth",
                        params={"symbol": symbol, "limit": 5})
        d = data.get("data", {})
        ask = float(d["asks"][0][0]) if d.get("asks") else get_price(symbol)
        bid = float(d["bids"][0][0]) if d.get("bids") else get_price(symbol)
        return bid, ask
    except Exception:
        p = get_price(symbol)
        return p, p

def get_klines(symbol, limit=50):
    try:
        data = safe_get(f"{BASE_URL}/openApi/spot/v2/market/kline",
                        params={"symbol": symbol, "interval": "1h", "limit": limit})
        return [float(k[4]) for k in data.get("data", [])]
    except Exception:
        return []

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)

def place_order(symbol, side, amount):
    return api_post("/openApi/spot/v1/trade/order", {
        "symbol": symbol, "side": side,
        "type": "MARKET", "quoteOrderQty": amount,
    })

def get_all_balances():
    data = api_get("/openApi/spot/v1/account/balance")
    result = []
    try:
        for b in data["data"]["balances"]:
            if float(b.get("free", 0)) > 0:
                result.append(f"• *{b['asset']}*: {float(b['free']):.6f}")
    except Exception:
        pass
    return result

def auth_check(update) -> bool:
    return not (ALLOWED_CHAT_ID and update.effective_chat.id != ALLOWED_CHAT_ID)

# ─── TREND ANALİZ ─────────────────────────────────────────────────────────────
def analyze_coin(symbol: str) -> dict:
    closes = get_klines(symbol)
    if len(closes) < 20:
        return {"symbol": symbol, "price": 0, "rsi": 50,
                "sma20": 0, "sma50": 0, "change": 0,
                "score": 0, "reasons": ["Məlumat çatışmır"]}
    price  = closes[-1]
    rsi    = calc_rsi(closes)
    sma20  = sum(closes[-20:]) / 20
    sma50  = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    change = ((closes[-1] - closes[-24]) / closes[-24]) * 100 if len(closes) >= 24 else 0
    score, reasons = 0, []
    if rsi < SETTINGS["rsi_oversold"]:
        score += 2; reasons.append(f"RSI={rsi} oversold")
    elif rsi < 45:
        score += 1; reasons.append(f"RSI={rsi} aşağı")
    elif rsi > SETTINGS["rsi_overbought"]:
        score -= 2; reasons.append(f"RSI={rsi} overbought")
    if sma20 > sma50:
        score += 1; reasons.append("Bullish trend")
    else:
        score -= 1; reasons.append("Bearish trend")
    if -3 < change < 0:
        score += 1; reasons.append(f"Cüzi düşüş {change:.1f}%")
    elif change < -5:
        score += 1; reasons.append(f"Böyük düşüş {change:.1f}%")
    return {"symbol": symbol, "price": round(price, 4), "rsi": rsi,
            "sma20": round(sma20, 4), "sma50": round(sma50, 4),
            "change": round(change, 2), "score": score, "reasons": reasons}

def find_best() -> list:
    results = []
    for s in WATCHLIST:
        try:
            results.append(analyze_coin(s))
        except Exception as e:
            logger.error(f"{s} analiz: {e}")
        time.sleep(0.2)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# ─── TRİANGULAR ARBİTRAJ ─────────────────────────────────────────────────────
def check_triangle(leg1, leg2, leg3, amount_usdt) -> dict | None:
    try:
        fee = 0.001
        _, ask1 = get_orderbook(leg1)
        if ask1 == 0: return None
        coin_a = (amount_usdt / ask1) * (1 - fee)

        _, ask2 = get_orderbook(leg2)
        if ask2 == 0: return None
        coin_b = (coin_a / ask2) * (1 - fee)

        bid3, _ = get_orderbook(leg3)
        if bid3 == 0: return None
        final_usdt = coin_b * bid3 * (1 - fee)

        profit_pct = ((final_usdt - amount_usdt) / amount_usdt) * 100
        return {
            "leg1": leg1, "leg2": leg2, "leg3": leg3,
            "start": amount_usdt, "end": round(final_usdt, 4),
            "profit_pct": round(profit_pct, 4),
            "profit_abs": round(final_usdt - amount_usdt, 4),
            "ask1": ask1, "ask2": ask2, "bid3": bid3,
        }
    except Exception as e:
        logger.error(f"Triangle {leg1}/{leg2}/{leg3}: {e}")
        return None

def scan_triangles(amount_usdt) -> list:
    results = []
    for t in TRIANGLES:
        r = check_triangle(t[0], t[1], t[2], amount_usdt)
        if r: results.append(r)
        time.sleep(0.2)
    results.sort(key=lambda x: x["profit_pct"], reverse=True)
    return results

def execute_triangle(t: dict):
    place_order(t["leg1"], "BUY", t["start"])
    time.sleep(0.5)
    coin_a = (t["start"] / t["ask1"]) * 0.999
    place_order(t["leg2"], "BUY", coin_a)
    time.sleep(0.5)
    coin_b = (coin_a / t["ask2"]) * 0.999
    place_order(t["leg3"], "SELL", coin_b)

# ─── KOMANDALAR ───────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    # Menyunu hər /start-da yenilə
    try:
        await context.bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        pass
    await update.message.reply_text(
        "🤖 *BingX Ultimate Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📈 *TREND TİCARƏTİ*\n"
        "• /scan — 8 coini analiz et\n"
        "• /best — Ən yaxşı fürsət\n"
        "• /status — Açıq mövqe\n"
        "• /trend on|off — Avto trend\n\n"
        "🔺 *TRİANGULAR ARBİTRAJ*\n"
        "• /arbscan — Arbitraj skan\n"
        "• /arb on|off — Avto arbitraj\n"
        "• /arbstats — Qazanc statistikası\n\n"
        "💼 *HESAB*\n"
        "• /balance — Balans\n"
        "• /watchlist — İzlənən coinlər\n"
        "• /add COIN — Əlavə et\n"
        "• /remove COIN — Sil\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Məbləğ: *{SETTINGS['trade_amount']} USDT*\n"
        f"📊 Min trend skoru: *{SETTINGS['min_score']}/4*\n"
        f"💹 Min arb qazancı: *{SETTINGS['arb_min_profit']}%*",
        parse_mode="Markdown"
    )

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ 8 coin analiz edilir...")
    try:
        results = find_best()
        lines   = ["🔍 *Bazar Analizi*\n━━━━━━━━━━━━━━━━━━━━"]
        for r in results:
            bar   = "█" * max(0, r["score"]) + "░" * max(0, 4 - r["score"])
            emoji = "🟢" if r["score"] >= 3 else "🟡" if r["score"] >= 1 else "🔴"
            lines.append(
                f"\n{emoji} *{r['symbol']}* [{bar}] {r['score']}/4\n"
                f"💵 ${r['price']:,.4f} | RSI:{r['rsi']} | {r['change']:+.1f}%"
            )
        best = results[0]
        lines.append(
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Ən yaxşı:* {best['symbol']} ({best['score']}/4)\n"
            + "\n".join(f"• {r}" for r in best["reasons"])
        )
        keyboard = [[
            InlineKeyboardButton(f"🟢 {best['symbol']} AL",
                                 callback_data=f"buy_{best['symbol']}"),
            InlineKeyboardButton("🔄 Yenilə", callback_data="rescan"),
        ]]
        await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ Analiz edilir...")
    try:
        results = find_best()
        best    = results[0]
        bar     = "█" * max(0, best["score"]) + "░" * max(0, 4 - best["score"])
        text = (
            f"🏆 *Ən Yaxşı Fürsət*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *{best['symbol']}*\n"
            f"💵 ${best['price']:,.4f}\n"
            f"📊 Skor: *{best['score']}/4* [{bar}]\n"
            f"RSI: {best['rsi']} | 24s: {best['change']:+.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
        for r in best["reasons"]: text += f"• {r}\n"
        text += "\n🎯 *AL siqnalı güclüdür!*" if best["score"] >= SETTINGS["min_score"] \
            else "\n⚠️ Skor aşağıdır, gözlə"
        keyboard = [[
            InlineKeyboardButton("🟢 AL", callback_data=f"buy_{best['symbol']}"),
            InlineKeyboardButton("🔄 Yenilə", callback_data="rebest"),
        ]]
        await msg.edit_text(text, parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    global current_position
    if not current_position:
        await update.message.reply_text(
            "📭 Açıq mövqe yoxdur\n*/best* ilə fürsət tap",
            parse_mode="Markdown"); return
    try:
        p     = current_position
        price = get_price(p["symbol"])
        pnl   = (price - p["entry_price"]) * p["qty"]
        pct   = ((price - p["entry_price"]) / p["entry_price"]) * 100
        dur   = datetime.now() - datetime.fromisoformat(p["time"])
        h     = int(dur.total_seconds() // 3600)
        m     = int((dur.total_seconds() % 3600) // 60)
        text  = (
            f"📌 *Açıq Mövqe*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 {p['symbol']}\n"
            f"💵 Giriş: ${p['entry_price']:,.4f}\n"
            f"💵 İndi:  ${price:,.4f}\n"
            f"⏱ {h}s {m}d\n"
            f"{'✅' if pnl >= 0 else '❌'} P&L: *${pnl:+.4f}* ({pct:+.2f}%)"
        )
        keyboard = [[
            InlineKeyboardButton("🔴 SAT", callback_data=f"sell_{p['symbol']}"),
            InlineKeyboardButton("🔄 Yenilə", callback_data="refresh_status"),
        ]]
        await update.message.reply_text(text, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"❌ Xəta: {e}")

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    try:
        bals = get_all_balances()
        text = "💼 *Hesab Balansı*\n━━━━━━━━━━━━━━━━━━━━\n"
        text += "\n".join(bals) if bals else "Balans tapılmadı"
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Xəta: {e}")

async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if not args or args[0] not in ["on", "off"]:
        await update.message.reply_text("❗ /trend on  yaxud  /trend off"); return
    SETTINGS["trend_auto"] = (args[0] == "on")
    s = "🟢 *AKTİVDİR*" if SETTINGS["trend_auto"] else "🔴 *DAYANDI*"
    await update.message.reply_text(f"📈 Trend ticarəti {s}", parse_mode="Markdown")

async def cmd_arb_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if not args or args[0] not in ["on", "off"]:
        await update.message.reply_text("❗ /arb on  yaxud  /arb off"); return
    SETTINGS["arb_auto"] = (args[0] == "on")
    s = "🟢 *AKTİVDİR*" if SETTINGS["arb_auto"] else "🔴 *DAYANDI*"
    await update.message.reply_text(f"🔺 Triangular arbitraj {s}", parse_mode="Markdown")

async def cmd_arbscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ Arbitraj imkanları axtarılır...")
    try:
        amount = SETTINGS["trade_amount"]
        opps   = scan_triangles(amount)
        lines  = ["🔺 *Triangular Arbitraj Skanu*\n━━━━━━━━━━━━━━━━━━━━"]
        for o in opps:
            emoji = "✅" if o["profit_pct"] > SETTINGS["arb_min_profit"] else "❌"
            lines.append(
                f"\n{emoji} *{o['leg1']}→{o['leg2']}→{o['leg3']}*\n"
                f"💵 {o['start']} → {o['end']} USDT\n"
                f"📊 Qazanc: *{o['profit_pct']:+.4f}%* (${o['profit_abs']:+.4f})"
            )
        best = opps[0] if opps else None
        if best and best["profit_pct"] > SETTINGS["arb_min_profit"]:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n🎯 *İcra edilə bilər!*")
            keyboard = [[
                InlineKeyboardButton("⚡ İCRA ET", callback_data="arb_exec_0"),
                InlineKeyboardButton("🔄 Yenilə", callback_data="arb_scan"),
            ]]
        else:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n⚠️ İndi qazanclı imkan yoxdur")
            keyboard = [[InlineKeyboardButton("🔄 Yenilə", callback_data="arb_scan")]]
        lines.append(f"\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data["last_arb"] = opps
    except Exception as e:
        await msg.edit_text(f"❌ Xəta: {e}")

async def cmd_arbstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    await update.message.reply_text(
        f"📊 *Arbitraj Statistikası*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ İcra sayı: *{arb_stats['count']}*\n"
        f"💰 Ümumi qazanc: *${arb_stats['total_profit']:.4f}*",
        parse_mode="Markdown"
    )

async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    coins = "\n".join(f"• {c}" for c in WATCHLIST)
    await update.message.reply_text(
        f"📋 *İzlənən Coinlər*\n━━━━━━━━━━━━━━━━━━━━\n{coins}\n\n"
        "Əlavə: `/add COIN`  |  Silmək: `/remove COIN`",
        parse_mode="Markdown"
    )

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    if not context.args:
        await update.message.reply_text("❗ /add DOGE"); return
    symbol = context.args[0].upper() + "-USDT"
    if symbol in WATCHLIST:
        await update.message.reply_text(f"⚠️ {symbol} artıq var"); return
    WATCHLIST.append(symbol)
    await update.message.reply_text(f"✅ *{symbol}* əlavə edildi", parse_mode="Markdown")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    if not context.args:
        await update.message.reply_text("❗ /remove DOGE"); return
    symbol = context.args[0].upper() + "-USDT"
    if symbol not in WATCHLIST:
        await update.message.reply_text(f"⚠️ {symbol} siyahıda yoxdur"); return
    WATCHLIST.remove(symbol)
    await update.message.reply_text(f"✅ *{symbol}* silindi", parse_mode="Markdown")

# ─── CALLBACK ─────────────────────────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_position, arb_stats
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "rescan":
        await cmd_scan(update, context)
    elif data == "rebest":
        await cmd_best(update, context)
    elif data == "arb_scan":
        await cmd_arbscan(update, context)
    elif data == "refresh_status":
        await cmd_status(update, context)

    elif data.startswith("buy_"):
        symbol = data[4:]
        amount = SETTINGS["trade_amount"]
        try:
            if current_position:
                old = current_position["symbol"]
                place_order(old, "SELL", amount)
                op  = get_price(old)
                pnl = (op - current_position["entry_price"]) * current_position["qty"]
                await query.message.reply_text(
                    f"🔄 SAT: {old} @ ${op:,.4f}\nP&L: {'✅' if pnl>=0 else '❌'} ${pnl:+.4f}",
                    parse_mode="Markdown"
                )
                current_position = None
            place_order(symbol, "BUY", amount)
            price = get_price(symbol)
            qty   = amount / price if price > 0 else 0
            current_position = {
                "symbol": symbol, "entry_price": price,
                "qty": qty, "time": datetime.now().isoformat()
            }
            await query.message.reply_text(
                f"✅ *AL: {symbol}*\n💵 ${price:,.4f} | 💰 {amount} USDT",
                parse_mode="Markdown"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")

    elif data.startswith("sell_"):
        symbol = data[5:]
        amount = SETTINGS["trade_amount"]
        try:
            place_order(symbol, "SELL", amount)
            price    = get_price(symbol)
            pnl_text = ""
            if current_position and current_position["symbol"] == symbol:
                pnl = (price - current_position["entry_price"]) * current_position["qty"]
                pct = ((price - current_position["entry_price"]) / current_position["entry_price"]) * 100
                pnl_text = f"\n{'✅' if pnl>=0 else '❌'} P&L: ${pnl:+.4f} ({pct:+.2f}%)"
                current_position = None
            await query.message.reply_text(
                f"✅ *SAT: {symbol}* @ ${price:,.4f}{pnl_text}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ {e}")

    elif data == "arb_exec_0":
        opps = context.user_data.get("last_arb", [])
        if not opps:
            await query.message.reply_text("❌ Arbitraj məlumatı tapılmadı"); return
        o = opps[0]
        try:
            execute_triangle(o)
            arb_stats["count"]        += 1
            arb_stats["total_profit"] += o["profit_abs"]
            await query.message.reply_text(
                f"⚡ *Arbitraj icra edildi!*\n"
                f"🔺 {o['leg1']} → {o['leg2']} → {o['leg3']}\n"
                f"💰 Qazanc: *${o['profit_abs']:+.4f}* ({o['profit_pct']:+.4f}%)\n"
                f"📊 Ümumi: ${arb_stats['total_profit']:.4f}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Arbitraj xətası: {e}")

# ─── AVTOMATIK İŞLƏR ─────────────────────────────────────────────────────────
async def trend_job(context: ContextTypes.DEFAULT_TYPE):
    global current_position
    if not SETTINGS["trend_auto"]: return
    try:
        results = find_best()
        best    = results[0]
        amount  = SETTINGS["trade_amount"]
        if best["score"] < SETTINGS["min_score"]: return
        if current_position and current_position["symbol"] == best["symbol"]: return
        if current_position:
            old = current_position["symbol"]
            place_order(old, "SELL", amount)
            op  = get_price(old)
            pnl = (op - current_position["entry_price"]) * current_position["qty"]
            await context.bot.send_message(
                chat_id=ALLOWED_CHAT_ID,
                text=f"🔄 *Mövqe dəyişir*\nSAT: {old} @ ${op:,.4f}\nP&L: ${pnl:+.4f}",
                parse_mode="Markdown"
            )
            current_position = None
        place_order(best["symbol"], "BUY", amount)
        price = get_price(best["symbol"])
        qty   = amount / price if price > 0 else 0
        current_position = {
            "symbol": best["symbol"], "entry_price": price,
            "qty": qty, "time": datetime.now().isoformat()
        }
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=(f"🤖 *OTO AL*\n🪙 {best['symbol']} @ ${price:,.4f}\n"
                  f"Skor: {best['score']}/4"),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Trend job: {e}")

async def arb_job(context: ContextTypes.DEFAULT_TYPE):
    global arb_stats
    if not SETTINGS["arb_auto"]: return
    try:
        amount = SETTINGS["trade_amount"]
        opps   = scan_triangles(amount)
        if not opps: return
        best = opps[0]
        if best["profit_pct"] < SETTINGS["arb_min_profit"]: return
        execute_triangle(best)
        arb_stats["count"]        += 1
        arb_stats["total_profit"] += best["profit_abs"]
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=(f"⚡ *OTO ARBİTRAJ*\n"
                  f"🔺 {best['leg1']}→{best['leg2']}→{best['leg3']}\n"
                  f"💰 Qazanc: *${best['profit_abs']:+.4f}* ({best['profit_pct']:+.4f}%)\n"
                  f"📊 Ümumi: ${arb_stats['total_profit']:.4f}"),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Arb job: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("best",      cmd_best))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("balance",   cmd_balance))
    app.add_handler(CommandHandler("trend",     cmd_trend))
    app.add_handler(CommandHandler("arb",       cmd_arb_toggle))
    app.add_handler(CommandHandler("arbscan",   cmd_arbscan))
    app.add_handler(CommandHandler("arbstats",  cmd_arbstats))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CommandHandler("remove",    cmd_remove))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.job_queue.run_repeating(trend_job, interval=300, first=30)
    app.job_queue.run_repeating(arb_job,   interval=120, first=60)

    print("🤖 BingX Ultimate Stable Bot v3 işə düşdü...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
