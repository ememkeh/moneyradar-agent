"""
MoneyRadar Agent — Reddit scanner
Uses Reddit's OAuth "script app" access (free — see README for the 2-minute setup).

Why OAuth instead of the plain .json endpoint: Reddit increasingly blocks unauthenticated
requests from cloud/datacenter IPs (which is what GitHub Actions runs on), causing silent
403 errors. OAuth script apps are free and don't have this problem.
"""
import os
import requests
import time
from config import SUBREDDITS

USER_AGENT = "MoneyRadarAgent/1.0 (personal referral-bonus tracker)"
_access_token_cache = {"token": None, "expires_at": 0}


def _get_access_token():
    """Fetch (and cache) an OAuth access token using script-app credentials."""
    now = time.time()
    if _access_token_cache["token"] and now < _access_token_cache["expires_at"]:
        return _access_token_cache["token"]

    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[reddit_scanner] REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — "
              "falling back to unauthenticated requests (may get blocked on cloud IPs).")
        return None

    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _access_token_cache["token"] = data["access_token"]
        _access_token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
        return _access_token_cache["token"]
    except requests.RequestException as e:
        print(f"[reddit_scanner] Failed to get OAuth token: {e}")
        return None


def fetch_new_posts(subreddit, limit=25):
    """Fetch the newest posts from a subreddit, using OAuth if credentials are available."""
    token = _get_access_token()

    if token:
        url = f"https://oauth.reddit.com/r/{subreddit}/new"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    else:
        url = f"https://www.reddit.com/r/{subreddit}/new.json"
        headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("children", [])
    except requests.RequestException as e:
        print(f"[reddit_scanner] Failed to fetch r/{subreddit}: {e}")
        return []


def scan_all_subreddits():
    """
    Scans every subreddit in config.SUBREDDITS.
    Returns a list of normalized post dicts:
    { id, source, title, body, url, permalink, subreddit }
    """
    all_posts = []
    for sub in SUBREDDITS:
        posts = fetch_new_posts(sub)
        for p in posts:
            post_data = p.get("data", {})
            all_posts.append({
                "id": f"reddit_{post_data.get('id')}",
                "source": "reddit",
                "title": post_data.get("title", ""),
                "body": post_data.get("selftext", ""),
                "url": post_data.get("url", ""),
                "permalink": f"https://reddit.com{post_data.get('permalink', '')}",
                "subreddit": sub,
            })
        time.sleep(1.5)
    return all_posts
