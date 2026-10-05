"""
MoneyRadar Agent – Main orchestrator
Run this on a schedule (every 2 hours via GitHub Actions – see .github/workflows/scan.yml).

What it does each run:
1. Scans X (if enabled + token present) and public Telegram channels (if enabled)
2. Filters posts: cash offers need an opportunity keyword + category + amount ≥ ₦500 / $1 / ₩1500;
   task airdrops and convertible points campaigns pass without a stated amount
   (rejects betting/gambling/gift-card/scam content regardless)
3. Skips posts already alerted on (dedupe via seen_posts.json)
4. Skips a second alert from the same X author on the same day (X only — exchange
   Telegram channels often post several real campaigns a day)
5. Runs a Claude Haiku check that confirms the post is real and live, picks the tier
   (💰 cash / 🪂 airdrop / 🎯 points) and writes a one-line English summary
6. Sends a Telegram alert for each new qualifying post
"""

import json
import os
from datetime import date

from config import SEEN_POSTS_FILE, MAX_SEEN_POSTS_STORED, TELEGRAM_ENABLED, TIER_LABELS
from x_scanner import scan_x
from telegram_scanner import scan_telegram_channels
from filters import evaluate_post
from ai_verify import classify_opportunity
from telegram_alert import send_alert, send_summary


class SeenPosts:
    """
    A set that remembers insertion order, so the oldest IDs are the ones
    dropped when the file hits MAX_SEEN_POSTS_STORED. (A plain set has no
    order — trimming it dropped random IDs, including fresh ones, which
    could cause repeat alerts.)
    """
    def __init__(self, ids=None):
        self._ids = dict.fromkeys(ids or [])

    def add(self, item):
        self._ids.pop(item, None)   # re-adding moves it to the newest end
        self._ids[item] = None

    def __contains__(self, item):
        return item in self._ids

    def __len__(self):
        return len(self._ids)

    def as_list(self):
        return list(self._ids)


def load_seen_posts():
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, "r") as f:
                return SeenPosts(json.load(f))
        except (ValueError, OSError) as e:
            print(f"Could not read {SEEN_POSTS_FILE} ({e}) — starting fresh.")
    return SeenPosts()


def save_seen_posts(seen_ids):
    # Keep the file from growing forever – drop oldest when over the cap.
    ids_list = seen_ids.as_list()
    if len(ids_list) > MAX_SEEN_POSTS_STORED:
        ids_list = ids_list[-MAX_SEEN_POSTS_STORED:]
    with open(SEEN_POSTS_FILE, "w") as f:
        json.dump(ids_list, f)


def run_scan():
    print("=== MoneyRadar Agent – starting scan ===")

    seen_ids = load_seen_posts()
    print(f"Loaded {len(seen_ids)} previously-seen post IDs.")

    reddit_posts = []  # Reddit disabled — no API access

    x_posts = scan_x()
    print(f"Fetched {len(x_posts)} posts from X.")

    telegram_posts = []
    if TELEGRAM_ENABLED:
        try:
            telegram_posts = scan_telegram_channels()
        except Exception as e:
            # A Telegram failure should never stop X alerts from going out
            print(f"[telegram] scanner failed, skipping this run: {e}")
    print(f"Fetched {len(telegram_posts)} posts from Telegram channels.")

    all_posts = reddit_posts + x_posts + telegram_posts
    new_alerts = 0
    today_str = date.today().isoformat()

    for post in all_posts:
        if post["id"] in seen_ids:
            continue  # already alerted on this one

        seen_ids.add(post["id"])  # mark as seen regardless of pass/fail — don't re-evaluate it next run

        result = evaluate_post(post["title"], post["body"])
        if not result["passes"]:
            continue  # silently skip — no need to log every non-match

        # Same-day author dedupe — X only. Checked here, but only recorded
        # after the AI approves, so a rejected post doesn't block that
        # author's genuine offer later the same day.
        author = post.get("author")
        author_key = None
        if author and post.get("source") != "telegram":
            author_key = f"authorday_{author}_{today_str}"
            if author_key in seen_ids:
                print(f"⏭️  SKIPPED (already alerted this author today): {post['title'][:60]}")
                continue

        # AI verification: confirms it's real + live, picks the tier, writes an English summary.
        verdict = classify_opportunity(post["title"], post["body"], result["tier_hint"])
        if not verdict["verified"]:
            print(f"🤖 AI REJECTED: {post['title'][:60]} — {verdict['reason']}")
            continue

        if author_key:
            seen_ids.add(author_key)

        # Extra fields for telegram_alert.py (ignored by older versions of it)
        post["tier"] = verdict["tier"]
        post["tier_label"] = TIER_LABELS.get(verdict["tier"], "")
        post["summary_en"] = verdict["summary_en"]

        print(f"✅ MATCH [{verdict['tier']}]: {post['title'][:80]} ({result['matched_amount']})")
        success = send_alert(post, result["matched_amount"])
        if success:
            new_alerts += 1

    save_seen_posts(seen_ids)
    send_summary(new_alerts, len(all_posts))

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()
