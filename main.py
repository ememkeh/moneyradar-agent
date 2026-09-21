"""
MoneyRadar Agent — Main orchestrator
Run this on a schedule (hourly via GitHub Actions — see .github/workflows/scan.yml).

What it does each run:
1. Scans Reddit (always) and X (if enabled + token present)
2. Filters posts for: opportunity keyword + fintech/crypto/wallet category + amount ≥ ₦500 or $1
   (rejects betting/gambling/gift-card content and generic noise formats regardless of amount)
3. Skips posts already alerted on (dedupe via seen_posts.json)
4. Skips a second alert from the same X author on the same day (prevents one
   prolific account's daily thread from generating repeat pings)
5. Runs a Claude Haiku check on anything that survives steps 2-4, to catch vague
   hype and airdrop-farming pitches that keyword-matching alone can't tell apart
   from a genuine offer (skipped automatically if ANTHROPIC_API_KEY isn't set)
6. Sends a Telegram alert for each new qualifying post
"""
import json
import os
from datetime import date

from config import SEEN_POSTS_FILE, MAX_SEEN_POSTS_STORED
from reddit_scanner import scan_all_subreddits
from x_scanner import scan_x
from filters import evaluate_post
from ai_verify import verify_opportunity
from telegram_alert import send_alert, send_summary


def load_seen_posts():
    if os.path.exists(SEEN_POSTS_FILE):
        with open(SEEN_POSTS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_posts(seen_ids):
    # Keep the file from growing forever — drop oldest when over the cap.
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
    today_str = date.today().isoformat()

    for post in all_posts:
        if post["id"] in seen_ids:
            continue  # already alerted on this one

        seen_ids.add(post["id"])  # mark as seen regardless of pass/fail — don't re-evaluate it next run

        result = evaluate_post(post["title"], post["body"])
        if not result["passes"]:
            continue  # silently skip — no need to log every non-match

        # Same-day author dedupe: skip if this author already triggered an alert today.
        author = post.get("author")
        if author:
            author_key = f"authorday_{author}_{today_str}"
            if author_key in seen_ids:
                print(f"⏭️  SKIPPED (already alerted this author today): {post['title'][:60]}")
                continue
            seen_ids.add(author_key)

        # AI verification: catches vague hype / airdrop-farming pitches / expired
        # offers that keyword-matching alone can't distinguish from a real offer.
        verified, ai_reason = verify_opportunity(post["title"], post["body"])
        if not verified:
            print(f"🤖 AI REJECTED: {post['title'][:60]} — {ai_reason}")
            continue

        print(f"✅ MATCH: {post['title'][:80]} ({result['matched_amount']})")
        success = send_alert(post, result["matched_amount"])
        if success:
            new_alerts += 1

    save_seen_posts(seen_ids)
    send_summary(new_alerts, len(all_posts))

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()
