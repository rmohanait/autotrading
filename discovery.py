"""
discovery.py — Stock discovery from Ripster's public channels.

Two methods:
  A) TradingView scraper (free) — scrapes ripster47's published ideas page
  B) X/Twitter API (optional, pay-per-use) — reads recent @ripster47 tweets
  C) Screenshot analysis — placeholder for Claude-based ticker extraction
     (you paste the screenshot into the chat and Claude returns tickers)

Returns a list of ticker symbols to add to the watchlist.
"""

import logging
import re
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# Common stock ticker pattern: 1-5 uppercase letters, optionally preceded by $
TICKER_PATTERN = re.compile(r'\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\b')

# Words that look like tickers but aren't — filter these out
TICKER_BLACKLIST = {
    "THE", "FOR", "AND", "BUT", "NOT", "ARE", "ALL", "NEW", "NOW",
    "GET", "OUT", "USE", "CEO", "CFO", "NYSE", "ETF", "IPO", "ATH",
    "SPY", "QQQ", "VIX", "USD", "DXY", "GDP", "FED", "SEC", "EOD",
    "EMA", "RSI", "ATR", "EPS", "PE", "AI", "US", "UK", "EU", "AM",
    "PM", "OR", "IF", "AT", "BE", "DO", "GO", "MY", "ON", "SO", "UP",
    "A", "I",
}


def extract_tickers_from_text(text: str) -> list[str]:
    """
    Extract stock tickers from a block of text (tweet, idea title, etc.).
    Prioritises $TICKER format (explicit), then UPPERCASE words.
    """
    found = set()

    # Priority: explicit $TICKER mentions
    dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', text.upper())
    found.update(dollar_tickers)

    # Secondary: plain uppercase words (less reliable)
    if not found:
        plain = re.findall(r'\b([A-Z]{2,5})\b', text.upper())
        found.update(t for t in plain if t not in TICKER_BLACKLIST)

    return sorted(found)


class StockDiscovery:
    """
    Discovers tickers from Ripster's public channels.
    """

    def __init__(self):
        self._twitter_enabled = (
            config.USE_TWITTER_FOR_DISCOVERY
            and bool(config.TWITTER_BEARER_TOKEN)
        )
        if self._twitter_enabled:
            logger.info("Stock discovery: using X/Twitter API")
        else:
            logger.info("Stock discovery: using TradingView scraper (free)")

    def get_ripster_picks(self) -> list[str]:
        """
        Returns a deduplicated list of ticker symbols Ripster has
        recently mentioned. Falls back to DEFAULT_WATCHLIST on error.
        """
        tickers = []

        # Skip scraping if disabled — use only the curated default watchlist
        if not getattr(config, "USE_SCRAPER_DISCOVERY", True):
            logger.info("Scraper discovery disabled — using DEFAULT_WATCHLIST only")
            return list(config.DEFAULT_WATCHLIST)

        try:
            if self._twitter_enabled:
                tickers = self._from_twitter()
            else:
                tickers = self._from_tradingview()
        except Exception as e:
            logger.error(f"Stock discovery failed: {e}")

        # Filter scraped tickers — only keep ones that look like real stocks
        # (must be 2-5 chars, not in blacklist, not pure navigation words)
        valid_tickers = [
            t for t in tickers
            if t not in TICKER_BLACKLIST
            and len(t) >= 2
            and len(t) <= 5
            and t.isalpha()
        ]

        if not valid_tickers:
            logger.info("Discovery returned no valid tickers — using DEFAULT_WATCHLIST")

        # Always start from the default watchlist, add valid scraped tickers
        all_tickers = list(dict.fromkeys(config.DEFAULT_WATCHLIST + valid_tickers))
        logger.info(f"Active watchlist: {all_tickers}")
        return all_tickers

    # ── TradingView Scraper (Free) ────────────────────────────────────

    def _from_tradingview(self) -> list[str]:
        """
        Scrapes ripster47's public TradingView ideas page.
        Extracts tickers mentioned in recent idea titles.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("beautifulsoup4 not installed. Run: pip install beautifulsoup4")
            return []

        try:
            # TradingView public ideas page for ripster47
            url = "https://www.tradingview.com/u/ripster47/#ideas"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Look for idea titles — TradingView renders some content server-side
            tickers_found = set()

            # Extract from page title text and any visible idea cards
            for tag in soup.find_all(["h3", "h2", "a", "span"]):
                text = tag.get_text(strip=True)
                if len(text) < 3 or len(text) > 200:
                    continue
                tickers = extract_tickers_from_text(text)
                tickers_found.update(tickers)

            if not tickers_found:
                logger.info("TradingView scraper found no tickers (page may be JS-rendered)")
                # Fallback: try the published scripts endpoint
                tickers_found = self._from_tradingview_scripts()

            result = [t for t in tickers_found if t not in TICKER_BLACKLIST]
            logger.info(f"TradingView scraper found: {result}")
            return result[:10]  # Cap at 10 new tickers per discovery run

        except requests.exceptions.RequestException as e:
            logger.warning(f"TradingView scraper request failed: {e}")
            return []

    def _from_tradingview_scripts(self) -> set[str]:
        """
        Fallback: fetch ripster47's published Pine scripts.
        Script titles often mention the ticker they're designed for.
        """
        try:
            url = (
                "https://www.tradingview.com/pine/get_published_scripts/?"
                "script_access=public&script_type=study&author_source=ripster47"
            )
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                items = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else []
                tickers = set()
                for item in (items if isinstance(items, list) else []):
                    title = item.get("scriptTitle", "") or item.get("title", "")
                    tickers.update(extract_tickers_from_text(title))
                return tickers
        except Exception:
            pass
        return set()

    # ── Twitter / X API ──────────────────────────────────────────────

    def _from_twitter(self) -> list[str]:
        """
        Fetch recent tweets from @ripster47 via X API (pay-per-use).
        Extracts tickers from tweet text.
        """
        try:
            # Resolve user ID for ripster47 (cached after first call)
            user_id = self._get_twitter_user_id(config.RIPSTER_TWITTER_USERNAME)
            if not user_id:
                return []

            url = f"https://api.twitter.com/2/users/{user_id}/tweets"
            headers = {"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"}
            params = {
                "max_results": 10,
                "tweet.fields": "created_at,text",
                "exclude": "retweets,replies",
            }
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            tweets = resp.json().get("data", [])

            tickers = set()
            for tweet in tweets:
                found = extract_tickers_from_text(tweet.get("text", ""))
                tickers.update(found)
                logger.debug(f"Tweet tickers: {found} from: {tweet.get('text','')[:60]}")

            result = [t for t in tickers if t not in TICKER_BLACKLIST]
            logger.info(f"Twitter discovery found: {result}")
            return result

        except Exception as e:
            logger.error(f"Twitter API call failed: {e}")
            return []

    def _get_twitter_user_id(self, username: str) -> Optional[str]:
        try:
            url = f"https://api.twitter.com/2/users/by/username/{username}"
            headers = {"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data", {}).get("id")
        except Exception as e:
            logger.error(f"Could not resolve Twitter user ID for @{username}: {e}")
            return None

    # ── Screenshot Analysis (Manual / Claude) ────────────────────────

    @staticmethod
    def parse_screenshot_text(text: str) -> list[str]:
        """
        When you paste a Ripster screenshot into Claude and Claude
        extracts the text, pass that text here to get the tickers.

        Example usage in main.py:
            tickers = StockDiscovery.parse_screenshot_text(claude_extracted_text)
        """
        tickers = extract_tickers_from_text(text)
        filtered = [t for t in tickers if t not in TICKER_BLACKLIST]
        logger.info(f"Screenshot text parsed — found tickers: {filtered}")
        return filtered
