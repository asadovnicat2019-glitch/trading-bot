import logging
import aiohttp

logger = logging.getLogger("Telegram")


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, config):
        self.token   = config.TG_BOT_TOKEN
        self.chat_id = config.TG_CHAT_ID

    async def send(self, text: str) -> bool:
        url = self.BASE.format(token=self.token)
        payload = {
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=10)) as r:
                    resp = await r.json()
                    if not resp.get("ok"):
                        logger.error(f"TG error: {resp}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"TG send failed: {e}")
            return False
