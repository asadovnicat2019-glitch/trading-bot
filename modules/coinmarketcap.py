import logging
import requests

logger = logging.getLogger("CMC")

STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP",
               "FRAX", "LUSD", "GUSD", "SUSD", "FDUSD", "PYUSD"}


class CoinMarketCapModule:
    BASE_URL = "https://pro-api.coinmarketcap.com/v1"

    def __init__(self, config):
        self.api_key = config.CMC_API_KEY

    def get_top_coins(self, limit: int = 20) -> list:
        url = f"{self.BASE_URL}/cryptocurrency/listings/latest"
        params = {
            "start": 1,
            "limit": limit + 10,
            "convert": "USDT",
            "sort": "market_cap",
        }
        headers = {
            "Accepts": "application/json",
            "X-CMC_PRO_API_KEY": self.api_key,
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            coins = []
            for item in data.get("data", []):
                sym = item["symbol"].upper()
                if sym in STABLECOINS:
                    continue
                coins.append({
                    "symbol": sym,
                    "name":   item["name"],
                    "rank":   item["cmc_rank"],
                    "price":  item["quote"]["USDT"]["price"],
                })
                if len(coins) >= limit:
                    break
            logger.info(f"Fetched {len(coins)} coins from CMC")
            return coins
        except Exception as e:
            logger.error(f"CMC fetch error: {e}")
            return []
