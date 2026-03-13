import logging
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import feedparser

logger = logging.getLogger("NewsRisk")


def _parse_time(entry) -> datetime:
    try:
        t = entry.get("published_parsed") or entry.get("updated_parsed")
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


class NewsRiskModule:
    def __init__(self, config):
        self.rss_feeds      = config.GOOGLE_ALERTS_RSS
        self.neg_keywords   = [k.lower() for k in config.NEGATIVE_KEYWORDS]
        self.lookback_min   = config.NEWS_LOOKBACK_MIN
        self.risk_threshold = config.RISK_THRESHOLD
        self._risk_counts   = defaultdict(int)
        self._last_refresh  = 0

    def refresh(self):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_min)
        new_counts = defaultdict(int)

        for coin, url in self.rss_feeds.items():
            if not url:
                continue
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    pub = _parse_time(entry)
                    if pub < cutoff:
                        continue
                    text = (
                        (entry.get("title",   "") or "") + " " +
                        (entry.get("summary", "") or "")
                    ).lower()
                    hits = sum(1 for kw in self.neg_keywords if kw in text)
                    if hits:
                        target = coin if coin.lower() != "general" and coin.lower() in text else "GENERAL"
                        new_counts[target] += hits
            except Exception as e:
                logger.warning(f"RSS fetch error for {coin}: {e}")

        self._risk_counts = new_counts
        self._last_refresh = time.time()
        logger.info(f"Risk counts updated: {dict(self._risk_counts)}")

    def is_risky(self, coin: str) -> bool:
        coin_hits    = self._risk_counts.get(coin.upper(), 0)
        general_hits = self._risk_counts.get("GENERAL", 0)
        total        = coin_hits + (general_hits // 2)
        risky = total >= self.risk_threshold
        if risky:
            logger.warning(f"{coin} risky: coin_hits={coin_hits}, general={general_hits}")
        return risky

    def get_summary(self) -> dict:
        return dict(self._risk_counts)
