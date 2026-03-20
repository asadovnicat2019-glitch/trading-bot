import logging

logger = logging.getLogger("Strategy")


def rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = prices[-period + i - 1] - prices[-period + i - 2]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


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

        if len(candles) < 20:
            return "HOLD"

        closes = [c["close"] for c in candles]
        rsi_val = rsi(closes, period=14)

        logger.debug(f"{symbol} RSI={rsi_val:.2f}")

        if rsi_val < 30:
            logger.info(f"BUY signal: {symbol} RSI={rsi_val:.2f}")
            return "BUY"
        elif rsi_val > 70:
            logger.info(f"SELL signal: {symbol} RSI={rsi_val:.2f}")
            return "SELL"

        return "HOLD"
