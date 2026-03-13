import logging
import statistics

logger = logging.getLogger("Strategy")


def ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema_vals = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema_vals.append(p * k + ema_vals[-1] * (1 - k))
    return ema_vals


class StrategyModule:
    def __init__(self, config, bingx):
        self.bingx  = bingx
        self.fast_p = config.EMA_FAST
        self.slow_p = config.EMA_SLOW

    async def get_signal(self, symbol: str) -> str:
        try:
            candles = await self.bingx.get_klines(symbol)
        except Exception as e:
            logger.error(f"Kline fetch error {symbol}: {e}")
            return "HOLD"

        if len(candles) < self.slow_p + 5:
            logger.warning(f"Not enough candles for {symbol}: {len(candles)}")
            return "HOLD"

        closes  = [c["close"]  for c in candles]
        volumes = [c["volume"] for c in candles]

        fast_ema = ema(closes, self.fast_p)
        slow_ema = ema(closes, self.slow_p)

        if len(fast_ema) < 2 or len(slow_ema) < 2:
            return "HOLD"

        f_now, f_prev = fast_ema[-1], fast_ema[-2]
        s_now, s_prev = slow_ema[-1], slow_ema[-2]

        avg_vol  = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else statistics.mean(volumes)
        vol_ok   = volumes[-1] > avg_vol * 1.2

        crossed_up   = f_prev <= s_prev and f_now > s_now
        crossed_down = f_prev >= s_prev and f_now < s_now

        if crossed_up and vol_ok:
            logger.info(f"BUY signal: {symbol}")
            return "BUY"
        elif crossed_down:
            logger.info(f"SELL signal: {symbol}")
            return "SELL"

        return "HOLD"
