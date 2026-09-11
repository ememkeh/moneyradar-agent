"""
MoneyRadar Agent — Main orchestrator
Run this on a schedule (every 6 hours via GitHub Actions — see .github/workflows/scan.yml).

What it does each run:
1. Scans Reddit (always) and X (if enabled + token present)
2. Filters posts for: referral keyword + fintech/crypto/wallet category + amount ≥ ₦500 or $1
3. Skips posts already alerted on (dedupe via seen_posts.json)
4. Sends a Telegram alert for each new qualifying post
"""
import json
import os

from config import SEEN_POSTS_FILE, MAX_SEEN_POSTS_STORED
from reddit_scanner import scan_all_subreddits
from x_scanner import scan_x
from filters import evaluate_post
from telegram_alert import send_alert, send_summary


def load_seen_posts():
    if os.path.exists(SEEN_POSTS_FILE):
        with open(SEEN_POSTS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_posts(seen_ids):
    ids_list = list(seen_ids)
    if len(ids_list) > MAX_SEEN_POSTS_STORED:
        ids_list = ids_list[-MAX_SEEN_POSTS_STORED:]
    with open(SEEN_POSTS_FILE, "w") as f:
        json.dump(ids_list, f)


def run_scan():
    print("=== MoneyRadar Agent — starting scan ===")

    seen_ids = load_seen_posts()
    print(f"Loaded {len(seen_ids)} previously-seen post IDs.")

    reddit_posts = scan_all_subreddits()
    print(f"Fetched {len(reddit_posts)} posts from Reddit.")

    x_posts = scan_x()
    print(f"Fetched {len(x_posts)} posts from X.")

    all_posts = reddit_posts + x_posts
    new_alerts = 0

    for post in all_posts:
        if post["id"] in seen_ids:
            continue

        seen_ids.add(post["id"])

        result = evaluate_post(post["title"], post["body"])
        if result["passes"]:
            print(f"✅ MATCH: {post['title'][:80]} ({result['matched_amount']})")
            success = send_alert(post, result["matched_amount"])
            if success:
                new_alerts += 1

    save_seen_posts(seen_ids)
    send_summary(new_alerts, len(all_posts))

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()
