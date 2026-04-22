"""
news_filter.py — Real-time news sentiment filter.

Before placing any trade, checks Marketaux (free tier: 100 req/day)
for recent news on the ticker. Blocks the trade if negative news
is found within the configured lookback window.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class NewsCache:
    """Simple in-memory cache to avoid burning through API quota."""
    def __init__(self, ttl_seconds: int = 300):
        self._cache: dict[str, tuple[bool, str, float]] = {}  # symbol → (is_safe, reason, timestamp)
        self._ttl = ttl_seconds

    def get(self, symbol: str) -> Optional[tuple[bool, str]]:
        if symbol in self._cache:
            is_safe, reason, ts = self._cache[symbol]
            if time.time() - ts < self._ttl:
                return is_safe, reason
        return None

    def set(self, symbol: str, is_safe: bool, reason: str):
        self._cache[symbol] = (is_safe, reason, time.time())


class NewsFilter:
    """
    Checks Marketaux API for recent news sentiment.
    Returns True (safe to trade) or False (block trade).
    """

    MARKETAUX_URL = "https://api.marketaux.com/v1/news/all"

    def __init__(self):
        self._cache = NewsCache(ttl_seconds=300)   # Cache 5 minutes
        self._api_key = config.NEWS_API_KEY
        self._enabled = config.ENABLE_NEWS_FILTER and bool(self._api_key)

        if not self._enabled:
            if not self._api_key:
                logger.warning("NEWS_API_KEY not set — news filter disabled. Trades will NOT be blocked by news.")
            else:
                logger.info("News filter disabled in config")

    def is_safe_to_trade(self, symbol: str) -> tuple[bool, str]:
        """
        Returns (is_safe, reason).
        If is_safe=False, the caller should NOT place the trade.
        """
        if not self._enabled:
            return True, "news filter disabled"

        # Check cache first
        cached = self._cache.get(symbol)
        if cached is not None:
            is_safe, reason = cached
            logger.debug(f"[{symbol}] News check (cached): {'safe' if is_safe else 'BLOCKED'} — {reason}")
            return is_safe, reason

        result = self._fetch_and_evaluate(symbol)
        self._cache.set(symbol, *result)
        return result

    def _fetch_and_evaluate(self, symbol: str) -> tuple[bool, str]:
        """Fetch news from Marketaux and evaluate sentiment."""
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=config.NEWS_LOOKBACK_HOURS))
            params = {
                "symbols": symbol,
                "filter_entities": "true",
                "language": "en",
                "published_after": since.strftime("%Y-%m-%dT%H:%M"),
                "api_token": self._api_key,
                "limit": 10,
            }
            resp = requests.get(self.MARKETAUX_URL, params=params, timeout=8)
            resp.raise_for_status()
            data = resp.json()

            articles = data.get("data", [])
            if not articles:
                logger.debug(f"[{symbol}] No recent news found — trade safe")
                return True, "no recent news"

            # Check each article title + description for negative keywords
            negative_hits = []
            for article in articles:
                text = (
                    (article.get("title") or "") + " " +
                    (article.get("description") or "")
                ).lower()
                for kw in config.NEGATIVE_KEYWORDS:
                    if kw.lower() in text:
                        negative_hits.append(f"'{kw}' in: {article.get('title', '')[:60]}")
                        break

                # Also use Marketaux's own sentiment score if available
                entities = article.get("entities", [])
                for entity in entities:
                    if entity.get("symbol") == symbol:
                        sentiment = entity.get("sentiment_score", 0)
                        if sentiment < -0.4:
                            negative_hits.append(
                                f"negative sentiment score ({sentiment:.2f}) on: {article.get('title', '')[:60]}"
                            )
                        break

            if negative_hits:
                reason = f"negative news detected: {negative_hits[0]}"
                logger.warning(f"[{symbol}] NEWS BLOCK — {reason}")
                return False, reason

            logger.info(f"[{symbol}] News check passed ({len(articles)} articles, no negatives)")
            return True, f"{len(articles)} recent articles, all clear"

        except requests.exceptions.Timeout:
            logger.warning(f"[{symbol}] News API timeout — allowing trade (fail-open)")
            return True, "news API timeout (fail-open)"

        except Exception as e:
            logger.warning(f"[{symbol}] News API error: {e} — allowing trade (fail-open)")
            return True, f"news API error: {e}"

    def get_headlines(self, symbol: str, limit: int = 5) -> list[str]:
        """Fetch recent headlines for display in the daily summary."""
        if not self._api_key:
            return ["No news API key configured"]
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=8))
            params = {
                "symbols": symbol,
                "filter_entities": "true",
                "language": "en",
                "published_after": since.strftime("%Y-%m-%dT%H:%M"),
                "api_token": self._api_key,
                "limit": limit,
            }
            resp = requests.get(self.MARKETAUX_URL, params=params, timeout=8)
            resp.raise_for_status()
            articles = resp.json().get("data", [])
            return [a.get("title", "No title") for a in articles]
        except Exception as e:
            return [f"Could not fetch headlines: {e}"]
