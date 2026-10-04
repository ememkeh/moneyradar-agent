"""
MoneyRadar Agent – X (Twitter) scanner
Uses TwitterAPI.io – a third-party paid data provider, NOT the official X API.

Why a third-party provider: X's official API costs ~$0.005 per read with no
subscription tier under $200/month for meaningful access. TwitterAPI.io charges
about $0.15 per 1,000 tweets returned.

Cost at the current setup (every 2 hours = 12 runs/day, ~20 tweets per query):
  12 English + 6 Chinese + 3 Korean = 21 queries/run
  21 x 12 x 20 = ~5,000 tweets/day = ~$0.75/day = ~$23/month
To cut cost, remove queries in config.py or set X_LANGUAGES below to ["en"].

Trade-off: this is NOT an official X partner. It's a data reseller. If X changes
how it blocks scraping, this could stop working with no advance notice.

Setup: sign up at https://twitterapi.io/dashboard, get an API key, set it as the
TWITTERAPI_IO_KEY environment variable (GitHub Actions secret), and keep
X_ENABLED = True in config.py.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import requests
import config
from config import X_SEARCH_QUERIES, X_ENABLED

TWITTERAPI_IO_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"

# Which language sets to run. Remove "zh" or "ko" to switch them off.
X_LANGUAGES = ["en", "zh", "ko"]

# Only fetch tweets from the last N days. Runs are every 2 hours and dedupe
# catches repeats, so a short window keeps expired offers out.
# (Was 30 days — most of what came back that old had already ended.)
X_LOOKBACK_DAYS = 3


def _query_sets():
    """Use the multi-language sets from config.py; fall back to English-only."""
    sets = getattr(config, "X_SEARCH_QUERY_SETS", None)
    if not sets:
        sets = [{"lang": "en", "queries": X_SEARCH_QUERIES}]
    return [s for s in sets if s.get("lang") in X_LANGUAGES and s.get("queries")]


def scan_x():
    """
    Returns a list of normalized post dicts from X via TwitterAPI.io, or an
    empty list if X_ENABLED is False or no API key is configured.
    """
    if not X_ENABLED:
        print("[x_scanner] X_ENABLED is False in config.py — skipping X scan.")
        return []

    api_key = os.environ.get("TWITTERAPI_IO_KEY")
    if not api_key:
        print("[x_scanner] X_ENABLED is True but TWITTERAPI_IO_KEY env var is missing — skipping X scan.")
        print("[x_scanner] Set TWITTERAPI_IO_KEY as a GitHub Actions secret to activate X scanning.")
        return []

    headers = {"X-API-Key": api_key}
    all_posts = []
    since_dt = datetime.now(timezone.utc) - timedelta(days=X_LOOKBACK_DAYS)
    since_timestamp = int(since_dt.timestamp())

    first = True
    for qset in _query_sets():
        lang = qset["lang"]
        lang_count = 0

        for query in qset["queries"]:
            if not first:
                time.sleep(2)  # avoid 429 rate-limiting — space queries out
            first = False

            params = {
                "query": f"{query} lang:{lang} -filter:retweets since_time:{since_timestamp}",
                "queryType": "Latest",
            }
            try:
                resp = requests.get(TWITTERAPI_IO_URL, headers=headers, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                for tweet in data.get("tweets", []) or []:
                    tweet_id = tweet.get("id", "")
                    if not tweet_id:
                        continue
                    author = tweet.get("author", {}) or {}
                    text = tweet.get("text", "") or ""
                    url = tweet.get("url") or f"https://x.com/i/web/status/{tweet_id}"
                    all_posts.append({
                        "id": f"x_{tweet_id}",
                        "source": "x",
                        "lang": lang,
                        "title": text[:100],
                        "body": text,
                        "url": url,
                        "permalink": url,
                        "subreddit": None,
                        "author": author.get("userName", ""),
                        "created_at": tweet.get("createdAt", ""),
                    })
                    lang_count += 1
            except requests.RequestException as e:
                print(f"[x_scanner] Failed to search '{query}': {e}")
            except ValueError as e:
                print(f"[x_scanner] Bad JSON for '{query}': {e}")

        print(f"[x_scanner] {lang}: {lang_count} tweets from {len(qset['queries'])} queries")

    # Same tweet can match several queries — keep the first copy only
    unique = {}
    for p in all_posts:
        unique.setdefault(p["id"], p)
    return list(unique.values())
