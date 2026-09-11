"""
MoneyRadar Agent — X (Twitter) scanner

IMPORTANT — read before enabling:
X's free API tier does NOT include search access as of 2026. To use this module you
need a paid X API Bearer Token (Basic tier or higher, currently priced on X's
developer portal — check https://developer.x.com/en/portal/products for current rates).

Set the environment variable X_BEARER_TOKEN and flip X_ENABLED = True in config.py
to activate this. Until then, the agent runs fine on Reddit alone — this module
will just log a warning and skip itself.
"""
import os
import requests
from config import X_SEARCH_QUERIES, X_ENABLED

X_API_URL = "https://api.x.com/2/tweets/search/recent"


def scan_x():
    """
    Returns a list of normalized post dicts from X, or an empty list if
    X_ENABLED is False or no bearer token is configured.
    """
    if not X_ENABLED:
        print("[x_scanner] X_ENABLED is False in config.py — skipping X scan.")
        return []

    bearer_token = os.environ.get("X_BEARER_TOKEN")
    if not bearer_token:
        print("[x_scanner] X_ENABLED is True but X_BEARER_TOKEN env var is missing — skipping X scan.")
        print("[x_scanner] Set X_BEARER_TOKEN as a GitHub Actions secret to activate X scanning.")
        return []

    headers = {"Authorization": f"Bearer {bearer_token}"}
    all_posts = []

    for query in X_SEARCH_QUERIES:
        params = {
            "query": f"{query} -is:retweet lang:en",
            "max_results": 25,
            "tweet.fields": "created_at,author_id",
        }
        try:
            resp = requests.get(X_API_URL, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for tweet in data.get("data", []):
                all_posts.append({
                    "id": f"x_{tweet.get('id')}",
                    "source": "x",
                    "title": tweet.get("text", "")[:100],
                    "body": tweet.get("text", ""),
                    "url": f"https://x.com/i/web/status/{tweet.get('id')}",
                    "permalink": f"https://x.com/i/web/status/{tweet.get('id')}",
                    "subreddit": None,
                })
        except requests.RequestException as e:
            print(f"[x_scanner] Failed to search '{query}': {e}")

    return all_posts
