import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    CMC_API_KEY: str  = os.getenv("CMC_API_KEY", "YOUR_CMC_KEY")
    CMC_TOP_N:   int  = int(os.getenv("CMC_TOP_N", "20"))

    BINGX_API_KEY:    str = os.getenv("BINGX_API_KEY", "YOUR_BINGX_KEY")
    BINGX_API_SECRET: str = os.getenv("BINGX_API_SECRET", "YOUR_BINGX_SECRET")
    BINGX_BASE_URL:   str = "https://open-api.bingx.com"
    TRADE_MODE:       str = os.getenv("TRADE_MODE", "spot")
    ORDER_USDT_SIZE:  float = float(os.getenv("ORDER_USDT_SIZE", "10"))

    KLINE_INTERVAL: str = os.getenv("KLINE_INTERVAL", "15m")
    EMA_FAST:       int = int(os.getenv("EMA_FAST", "20"))
    EMA_SLOW:       int = int(os.getenv("EMA_SLOW", "50"))
    KLINE_LIMIT:    int = 100

    GOOGLE_ALERTS_RSS: dict = {
        "BTC":     os.getenv("RSS_BTC", ""),
        "ETH":     os.getenv("RSS_ETH", ""),
        "SOL":     os.getenv("RSS_SOL", ""),
        "GENERAL": os.getenv("RSS_GENERAL", "https://cointelegraph.com/rss"),
    }
    NEGATIVE_KEYWORDS: list = [
        "hack", "hacked", "exploit", "ban", "banned",
        "lawsuit", "sec", "scam", "rug", "exit scam",
        "ponzi", "fraud", "arrest", "seized", "shutdown",
        "delist", "delisted", "investigation", "fine",
    ]
    NEWS_LOOKBACK_MIN: int  = int(os.getenv("NEWS_LOOKBACK_MIN", "60"))
    RISK_THRESHOLD:    int  = int(os.getenv("RISK_THRESHOLD", "2"))

    TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "YOUR_BOT_TOKEN")
    TG_CHAT_ID:   str = os.getenv("TG_CHAT_ID",   "YOUR_CHAT_ID")

    LOOP_INTERVAL_SEC: int = int(os.getenv("LOOP_INTERVAL_SEC", "900"))
