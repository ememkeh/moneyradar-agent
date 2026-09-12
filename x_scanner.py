"""
MoneyRadar Agent — X (Twitter) scanner
Uses TwitterAPI.io — a third-party paid data provider, NOT the official X API.

Why a third-party provider: X's official API costs ~$0.005 per read with no
subscription tier under $200/month for meaningful access. TwitterAPI.io charges
$0.15 per 1,000 tweets (about $0.00015 per tweet) — at our scan volume (4 queries
x 4 runs/day x ~20 results = ~320 tweets/day, ~9,600/month) that's roughly
$1.44/month instead of ~$48-60/month on the official API.

Trade-off: this is NOT an official X partner. It's a data reseller. If X changes
how it blocks scraping, this could stop working with no advance notice. Treat it
as "good enough for a personal side project," not as infrastructure to depend on.

Setup: sign up at https://twitterapi.io/dashboard, get an API key, set it as the
TWITTERAPI_IO_KEY environment variable (GitHub Actions secret), and flip
X_ENABLED = True in config.py.
"""
import os
import requests
from config import X_SEARCH_QUERIES, X_ENABLED

TWITTERAPI_IO_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"


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

    for query in X_SEARCH_QUERIES:
        params = {
            "query": f"{query} lang:en -filter:retweets",
            "queryType": "Latest",
        }
        try:
            resp = requests.get(TWITTERAPI_IO_URL, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for tweet in data.get("tweets", []):
                tweet_id = tweet.get("id", "")
                author = tweet.get("author", {}) or {}
                all_posts.append({
                    "id": f"x_{tweet_id}",
                    "source": "x",
                    "title": tweet.get("text", "")[:100],
                    "body": tweet.get("text", ""),
                    "url": tweet.get("url") or f"https://x.com/i/web/status/{tweet_id}",
                    "permalink": tweet.get("url") or f"https://x.com/i/web/status/{tweet_id}",
                    "subreddit": None,
                    "author": author.get("userName", ""),
                })
        except requests.RequestException as e:
            print(f"[x_scanner] Failed to search '{query}': {e}")

    return all_posts
