import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger("BingX")


def _sign(secret: str, params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BingXModule:
    SPOT_BASE    = "https://open-api.bingx.com"
    FUTURES_BASE = "https://open-api.bingx.com"

    def __init__(self, config):
        self.api_key    = config.BINGX_API_KEY
        self.api_secret = config.BINGX_API_SECRET
        self.mode       = config.TRADE_MODE.lower()
        self.kline_int  = config.KLINE_INTERVAL
        self.kline_lim  = config.KLINE_LIMIT

    def _headers(self) -> dict:
        return {"X-BX-APIKEY": self.api_key}

    def _ts(self) -> int:
        return int(time.time() * 1000)

    def _signed_params(self, params: dict) -> dict:
        params["timestamp"] = self._ts()
        params["signature"] = _sign(self.api_secret, params)
        return params

    async def get_available_symbols(self) -> set:
        if self.mode == "futures":
            url = f"{self.FUTURES_BASE}/openApi/swap/v2/quote/contracts"
        else:
            url = f"{self.SPOT_BASE}/openApi/spot/v1/common/symbols"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers(),
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)

        symbols = set()
        if self.mode == "futures":
            for item in data.get("data", []):
                base = item.get("symbol", "").split("-")[0].upper()
                if base:
                    symbols.add(base)
        else:
            for item in data.get("data", {}).get("symbols", []):
                base = item.get("symbol", "").split("-")[0].upper()
                if base:
                    symbols.add(base)
        return symbols

    def filter_tradable(self, cmc_coins: list, bingx_symbols: set) -> list:
        return [c["symbol"] for c in cmc_coins if c["symbol"] in bingx_symbols]

    async def get_klines(self, symbol: str, interval: str = None, limit: int = None) -> list:
        interval = interval or self.kline_int
        limit    = limit    or self.kline_lim

        if self.mode == "futures":
            url    = f"{self.FUTURES_BASE}/openApi/swap/v3/quote/klines"
            params = {"symbol": symbol, "interval": interval, "limit": limit}
        else:
            url    = f"{self.SPOT_BASE}/openApi/spot/v2/market/kline"
            params = {"symbol": symbol, "interval": interval, "limit": limit}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=self._headers(),
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)

        raw = data.get("data", [])
        candles = []
        for c in raw:
            candles.append({
                "open":   float(c.get("o") or c.get("open",  0)),
                "high":   float(c.get("h") or c.get("high",  0)),
                "low":    float(c.get("l") or c.get("low",   0)),
                "close":  float(c.get("c") or c.get("close", 0)),
                "volume": float(c.get("v") or c.get("volume", 0)),
            })
        return candles

    async def place_order(self, symbol: str, side: str, usdt_size: float):
        logger.info(f"[ORDER] {side} {symbol} ~{usdt_size} USDT")
        if self.mode == "futures":
            return await self._futures_order(symbol, side, usdt_size)
        else:
            return await self._spot_order(symbol, side, usdt_size)

    async def _spot_order(self, symbol: str, side: str, usdt_size: float):
        url = f"{self.SPOT_BASE}/openApi/spot/v1/trade/order"
        params = self._signed_params({
            "symbol":        symbol,
            "side":          side,
            "type":          "MARKET",
            "quoteOrderQty": str(usdt_size),
        })
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params, headers=self._headers(),
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)
        if data.get("code") == 0:
            order = data.get("data", {}).get("order", {})
            logger.info(f"Spot order OK: {order}")
            return order
        else:
            logger.error(f"Spot order error: {data}")
            return None

    async def _futures_order(self, symbol: str, side: str, usdt_size: float):
        url = f"{self.FUTURES_BASE}/openApi/swap/v2/trade/order"
        params = self._signed_params({
            "symbol":        symbol,
            "side":          side,
            "positionSide":  "LONG" if side == "BUY" else "SHORT",
            "type":          "MARKET",
            "quoteOrderQty": str(usdt_size),
        })
        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=params, headers=self._headers(),
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)
        if data.get("code") == 0:
            order = data.get("data", {}).get("order", {})
            logger.info(f"Futures order OK: {order}")
            return order
        else:
            logger.error(f"Futures order error: {data}")
            return None
