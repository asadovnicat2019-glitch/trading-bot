"""
BingX Ultimate Trading Bot
===========================
Rejim 1 — Smart Trend: 8 coini analiz edir, ən güclü siqnalı alır
Rejim 2 — Triangular Arbitraj: BingX daxilində üçbucaq arbitrajı axtarır
           USDT → A → B → USDT  (qazanc varsa icra edir)
"""

import os
import logging
import hmac
import hashlib
import time
import requests
from datetime import datetime
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

# ─── WATCHLIST ────────────────────────────────────────────────────────────────
WATCHLIST = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT",
    "BNB-USDT", "DOGE-USDT", "AVAX-USDT", "MATIC-USDT",
]

# Triangular arbitraj üçün yollar: USDT → A → B → USDT
# BingX-də mövcud olan cross-cütlər
TRIANGLES = [
    ("BTC-USDT", "ETH-BTC",  "ETH-USDT"),   # USDT→BTC→ETH→USDT
    ("BTC-USDT", "BNB-BTC",  "BNB-USDT"),   # USDT→BTC→BNB→USDT
    ("ETH-USDT", "SOL-ETH",  "SOL-USDT"),   # USDT→ETH→SOL→USDT
    ("BTC-USDT", "XRP-BTC",  "XRP-USDT"),   # USDT→BTC→XRP→USDT
    ("ETH-USDT", "MATIC-ETH","MATIC-USDT"), # USDT→ETH→MATIC→USDT
]

SETTINGS = {
    "trade_amount":    10.0,   # USDT
    "rsi_oversold":    30,
    "rsi_overbought":  70,
    "min_score":       3,      # Trend ticarəti üçün min skor
    "arb_min_profit":  0.3,    # Minimum arbitraj qazancı (%)
    "trend_auto":      False,
    "arb_auto":        False,
}

current_position = None   # Trend mövqesi
arb_stats = {"total_profit": 0.0, "count": 0}

# ─── API ──────────────────────────────────────────────────────────────────────
def sign(params):
    q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(BINGX_API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

def api_post(path, params=None):
    if not params: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    r = requests.post(BASE_URL + path, params=params,
                      headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=10)
    return r.json()

def api_get(path, params=None):
    if not params: params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    r = requests.get(BASE_URL + path, params=params,
                     headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=10)
    return r.json()

def get_price(symbol):
    r = requests.get(f"{BASE_URL}/openApi/spot/v1/ticker/price",
                     params={"symbol": symbol}, timeout=10)
    return float(r.json()["data"]["price"])

def get_orderbook(symbol):
    """Bid/Ask qiymətlərini al — arbitraj üçün daha dəqiq."""
    r = requests.get(f"{BASE_URL}/openApi/spot/v1/market/depth",
                     params={"symbol": symbol, "limit": 5}, timeout=10)
    data = r.json().get("data", {})
    best_ask = float(data["asks"][0][0]) if data.get("asks") else get_price(symbol)
    best_bid = float(data["bids"][0][0]) if data.get("bids") else get_price(symbol)
    return best_bid, best_ask

def get_klines(symbol, limit=50):
    r = requests.get(f"{BASE_URL}/openApi/spot/v2/market/kline",
                     params={"symbol": symbol, "interval": "1h", "limit": limit},
                     timeout=10)
    return [float(k[4]) for k in r.json()["data"]]

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100
    return round(100 - (100 / (1 + ag/al)), 2)

def place_order(symbol, side, amount):
    return api_post("/openApi/spot/v1/trade/order", {
        "symbol": symbol, "side": side,
        "type": "MARKET", "quoteOrderQty": amount,
    })

def get_balance_usdt():
    data = api_get("/openApi/spot/v1/account/balance")
    if "data" in data and "balances" in data["data"]:
        for b in data["data"]["balances"]:
            if b["asset"] == "USDT":
                return float(b.get("free", 0))
    return 0.0

def get_all_balances():
    data = api_get("/openApi/spot/v1/account/balance")
    result = []
    if "data" in data and "balances" in data["data"]:
        for b in data["data"]["balances"]:
            if float(b.get("free", 0)) > 0:
                result.append(f"• *{b['asset']}*: {float(b['free']):.6f}")
    return result

def auth_check(update):
    return not (ALLOWED_CHAT_ID and update.effective_chat.id != ALLOWED_CHAT_ID)

# ─── TREND ANALİZ ─────────────────────────────────────────────────────────────
def analyze_coin(symbol):
    closes = get_klines(symbol)
    price  = closes[-1]
    rsi    = calc_rsi(closes)
    sma20  = sum(closes[-20:]) / 20
    sma50  = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    change = ((closes[-1] - closes[-24]) / closes[-24]) * 100 if len(closes) >= 24 else 0
    score  = 0
    reasons = []
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
    return {"symbol": symbol, "price": round(price, 4), "rsi": rsi,
            "sma20": round(sma20, 4), "sma50": round(sma50, 4),
            "change": round(change, 2), "score": score, "reasons": reasons}

def find_best():
    results = []
    for s in WATCHLIST:
        try:
            results.append(analyze_coin(s))
            time.sleep(0.2)
        except Exception as e:
            logger.error(f"{s}: {e}")
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# ─── TRİANGULAR ARBİTRAJ ─────────────────────────────────────────────────────
def check_triangle(leg1, leg2, leg3, amount_usdt):
    """
    USDT → Coin_A (leg1 alış)
    Coin_A → Coin_B (leg2 alış)
    Coin_B → USDT (leg3 satış)
    Qazanc varsa geri qaytar.
    """
    try:
        fee = 0.001  # BingX 0.1% komissiya

        # Leg 1: USDT → A  (A-USDT cütündən A alırıq)
        _, ask1 = get_orderbook(leg1)
        coin_a = (amount_usdt / ask1) * (1 - fee)

        # Leg 2: A → B  (B-A cütündən B alırıq)
        _, ask2 = get_orderbook(leg2)
        coin_b = (coin_a / ask2) * (1 - fee)

        # Leg 3: B → USDT  (B-USDT cütündən B satırıq)
        bid3, _ = get_orderbook(leg3)
        final_usdt = coin_b * bid3 * (1 - fee)

        profit_pct = ((final_usdt - amount_usdt) / amount_usdt) * 100
        profit_abs = final_usdt - amount_usdt

        return {
            "leg1": leg1, "leg2": leg2, "leg3": leg3,
            "start": amount_usdt, "end": round(final_usdt, 4),
            "profit_pct": round(profit_pct, 4),
            "profit_abs": round(profit_abs, 4),
            "ask1": ask1, "ask2": ask2, "bid3": bid3,
        }
    except Exception as e:
        logger.error(f"Triangle check xətası {leg1}/{leg2}/{leg3}: {e}")
        return None

def scan_triangles(amount_usdt):
    """Bütün üçbucaqları yoxla, qazanclı olanı tap."""
    opportunities = []
    for t in TRIANGLES:
        result = check_triangle(t[0], t[1], t[2], amount_usdt)
        if result:
            opportunities.append(result)
        time.sleep(0.2)
    opportunities.sort(key=lambda x: x["profit_pct"], reverse=True)
    return opportunities

def execute_triangle(t):
    """Üçbucaq arbitrajını icra et."""
    amount = t["start"]
    # Leg 1
    place_order(t["leg1"], "BUY", amount)
    time.sleep(0.5)
    # Leg 2 — coin_a miqdarını hesabla
    coin_a = (amount / t["ask1"]) * 0.999
    place_order(t["leg2"], "BUY", coin_a)
    time.sleep(0.5)
    # Leg 3 — coin_b miqdarını hesabla
    coin_b = (coin_a / t["ask2"]) * 0.999
    place_order(t["leg3"], "SELL", coin_b)

# ─── KOMANDALAR ───────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    await update.message.reply_text(
        "🤖 *BingX Ultimate Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📈 *TREND TİCARƏTİ*\n"
        "*/scan* — 8 coini analiz et\n"
        "*/best* — Ən yaxşı fürsət\n"
        "*/status* — Açıq mövqe\n"
        "*/trend on/off* — Avto trend\n\n"
        "🔺 *TRİANGULAR ARBİTRAJ*\n"
        "*/arb* — Arbitraj imkanlarını skan et\n"
        "*/arb on/off* — Avto arbitraj\n"
        "*/arbstats* — Ümumi qazanc statistikası\n\n"
        "💼 *HESAB*\n"
        "*/balance* — Balans\n"
        "*/watchlist* — İzlənən coinlər\n"
        "*/add COIN* — Coin əlavə et\n"
        "*/remove COIN* — Coin sil\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Məbləğ: *{SETTINGS['trade_amount']} USDT*\n"
        f"📊 Min trend skoru: *{SETTINGS['min_score']}/4*\n"
        f"💹 Min arbitraj qazancı: *{SETTINGS['arb_min_profit']}%*",
        parse_mode="Markdown"
    )

async def cmd_arb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    msg = await update.message.reply_text("⏳ Triangular arbitraj imkanları axtarılır...")
    try:
        amount = SETTINGS["trade_amount"]
        opps   = scan_triangles(amount)
        lines  = ["🔺 *Triangular Arbitraj Skanu*\n━━━━━━━━━━━━━━━━━━━━"]

        for o in opps:
            emoji = "✅" if o["profit_pct"] > SETTINGS["arb_min_profit"] else "❌"
            lines.append(
                f"\n{emoji} *{o['leg1']} → {o['leg2']} → {o['leg3']}*\n"
                f"💵 {o['start']} → {o['end']} USDT\n"
                f"📊 Qazanc: *{o['profit_pct']:+.4f}%* (${o['profit_abs']:+.4f})"
            )

        best = opps[0] if opps else None
        if best and best["profit_pct"] > SETTINGS["arb_min_profit"]:
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\n🎯 *İcra edilə bilər!*")
            keyboard = [[
                InlineKeyboardButton("⚡ İCRA ET", callback_data=f"arb_exec_0"),
                InlineKeyboardButton("🔄 Yenilə", callback_data="arb_scan"),
            ]]
        else:
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\n⚠️ İndi qazanclı imkan yoxdur")
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
        f"📊 *Arbitraj Statistikası*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ İcra sayı: *{arb_stats['count']}*\n"
        f"💰 Ümumi qazanc: *${arb_stats['total_profit']:.4f}*",
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
            f"✅ {', '.join(best['reasons'])}"
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
    msg = await update.message.reply_text("⏳ Ən yaxşı fürsət axtarılır...")
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
        if best["score"] >= SETTINGS["min_score"]:
            text += "\n🎯 *AL siqnalı güclüdür!*"
        else:
            text += f"\n⚠️ Skor aşağıdır, gözləmək tövsiyə olunur"
        keyboard = [[
            InlineKeyboardButton(f"🟢 AL", callback_data=f"buy_{best['symbol']}"),
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
            "📭 Açıq mövqe yoxdur\n*/best* — Fürsət tap",
            parse_mode="Markdown"
        )
        return
    try:
        p     = current_position
        price = get_price(p["symbol"])
        pnl   = (price - p["entry_price"]) * p["qty"]
        pct   = ((price - p["entry_price"]) / p["entry_price"]) * 100
        dur   = datetime.now() - datetime.fromisoformat(p["time"])
        h, m  = divmod(int(dur.total_seconds()), 3600)
        text  = (
            f"📌 *Açıq Mövqe*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 {p['symbol']}\n"
            f"💵 Giriş: ${p['entry_price']:,.4f}\n"
            f"💵 İndi: ${price:,.4f}\n"
            f"⏱ {h//3600}s {(h%3600)//60}d\n"
            f"{'✅' if pnl>=0 else '❌'} P&L: *${pnl:+.4f}* ({pct:+.2f}%)"
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
        await update.message.reply_text("❗ /trend on  yaxud  /trend off")
        return
    SETTINGS["trend_auto"] = (args[0] == "on")
    status = "🟢 *AKTİVDİR*" if SETTINGS["trend_auto"] else "🔴 *DAYANDI*"
    await update.message.reply_text(f"📈 Trend ticarəti {status}", parse_mode="Markdown")

async def cmd_arb_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update): return
    args = context.args
    if not args or args[0] not in ["on", "off"]:
        await update.message.reply_text("❗ /arb on  yaxud  /arb off")
        return
    SETTINGS["arb_auto"] = (args[0] == "on")
    status = "🟢 *AKTİVDİR*" if SETTINGS["arb_auto"] else "🔴 *DAYANDI*"
    await update.message.reply_text(f"🔺 Triangular arbitraj {status}", parse_mode="Markdown")

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

    if data == "rescan":       await cmd_scan(update, context)
    elif data == "rebest":     await cmd_best(update, context)
    elif data == "arb_scan":   await cmd_arb(update, context)
    elif data == "refresh_status": await cmd_status(update, context)

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
                    f"🔄 SAT: {old} @ ${op:,.4f} | P&L: {'✅' if pnl>=0 else '❌'} ${pnl:+.4f}",
                    parse_mode="Markdown"
                )
                current_position = None
            place_order(symbol, "BUY", amount)
            price = get_price(symbol)
            qty   = amount / price
            current_position = {"symbol": symbol, "entry_price": price,
                                 "qty": qty, "time": datetime.now().isoformat()}
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

    elif data.startswith("arb_exec_"):
        idx  = int(data.split("_")[-1])
        opps = context.user_data.get("last_arb", [])
        if not opps or idx >= len(opps):
            await query.message.reply_text("❌ Arbitraj məlumatı tapılmadı"); return
        o = opps[idx]
        try:
            execute_triangle(o)
            arb_stats["count"]        += 1
            arb_stats["total_profit"] += o["profit_abs"]
            await query.message.reply_text(
                f"⚡ *Arbitraj icra edildi!*\n"
                f"🔺 {o['leg1']} → {o['leg2']} → {o['leg3']}\n"
                f"💰 Qazanc: *${o['profit_abs']:+.4f}* ({o['profit_pct']:+.4f}%)\n"
                f"📊 Ümumi qazanc: ${arb_stats['total_profit']:.4f}",
                parse_mode="Markdown"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Arbitraj xətası: {e}")

# ─── AVTOMATIK İŞLƏR ─────────────────────────────────────────────────────────
async def trend_job(context: ContextTypes.DEFAULT_TYPE):
    """Hər 5 dəqiqə — trend ticarəti."""
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
                text=f"🔄 *Mövqe dəyişir*\nSAT: {old} @ ${op:,.4f} | P&L: ${pnl:+.4f}",
                parse_mode="Markdown"
            )
            current_position = None
        place_order(best["symbol"], "BUY", amount)
        price = get_price(best["symbol"])
        qty   = amount / price
        current_position = {"symbol": best["symbol"], "entry_price": price,
                             "qty": qty, "time": datetime.now().isoformat()}
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=(f"🤖 *OTO AL*\n🪙 {best['symbol']} @ ${price:,.4f}\n"
                  f"Skor: {best['score']}/4 | {', '.join(best['reasons'])}"),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Trend job xətası: {e}")

async def arb_job(context: ContextTypes.DEFAULT_TYPE):
    """Hər 2 dəqiqə — triangular arbitraj skanu."""
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
                  f"🔺 {best['leg1']} → {best['leg2']} → {best['leg3']}\n"
                  f"💰 Qazanc: *${best['profit_abs']:+.4f}* ({best['profit_pct']:+.4f}%)\n"
                  f"📊 Ümumi: ${arb_stats['total_profit']:.4f}"),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Arb job xətası: {e}")

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
    app.add_handler(CommandHandler("arbscan",   cmd_arb))
    app.add_handler(CommandHandler("arbstats",  cmd_arbstats))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CommandHandler("remove",    cmd_remove))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.job_queue.run_repeating(trend_job, interval=300, first=30)   # hər 5 dəq
    app.job_queue.run_repeating(arb_job,   interval=120, first=60)   # hər 2 dəq
    print("🤖 BingX Ultimate Bot işə düşdü...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
