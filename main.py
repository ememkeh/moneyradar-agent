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
import re
from difflib import SequenceMatcher
from datetime import date, timedelta

from config import SEEN_POSTS_FILE, MAX_SEEN_POSTS_STORED, TELEGRAM_ENABLED, TIER_LABELS
import config

# How far back to remember alerted campaigns. A repost of the same campaign
# (same reward + tasks) inside this window is skipped; a NEW campaign from the
# same project always alerts. (Override by adding PROJECT_DEDUPE_DAYS to config.py)
PROJECT_DEDUPE_DAYS = getattr(config, "PROJECT_DEDUPE_DAYS", 7)
from x_scanner import scan_x
from telegram_scanner import scan_telegram_channels
from filters import evaluate_post
from ai_verify import classify_opportunity, is_same_campaign
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

    def keys_starting(self, prefix):
        return [k for k in self._ids if k.startswith(prefix)]


# Alerted campaigns are stored inside seen_posts.json as text entries:
#   "alert|<project>|<YYYY-MM-DD>|<english summary>"
def _recent_campaigns(seen_ids, project, days):
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    summaries = []
    for key in seen_ids.keys_starting(f"alert|{project}|"):
        parts = key.split("|", 3)
        if len(parts) == 4 and parts[2] >= cutoff:
            summaries.append(parts[3])
    return summaries


# ── FREE COPY-PASTE CHECK (runs before Claude, costs nothing) ──
# KOLs often post near-identical template text. If a new post is almost the
# same text as one already alerted (or confirmed as a repeat), skip it without
# calling Claude. Safety rules so real opportunities aren't missed:
#   - only compares against posts already ALERTED in the last PROJECT_DEDUPE_DAYS
#   - needs >= 85% identical text (links and ref codes ignored)
#   - every number must match — a different reward amount, date or season
#     number always goes to Claude
COPY_SIMILARITY = 0.85
_URL_RE = re.compile(r"https?://\S+|t\.co/\S+|\b\w+\.(?:com|xyz|io|me|app|ai|org|net)/\S*", re.I)
_CODE_RE = re.compile(r"\b(?=\w*\d)(?=\w*[a-z])\w{6,}\b", re.I)   # ref codes like ff96d9c8c6e0
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _normalize(text):
    text = (text or "").lower()
    text = _URL_RE.sub(" ", text)
    text = _CODE_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)      # drop emoji/punctuation, keep CJK
    return re.sub(r"\s+", " ", text).strip()[:400]


def _numbers(norm):
    return set(_NUM_RE.findall(norm))


def _recent_texts(seen_ids, days):
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    texts = []
    for key in seen_ids.keys_starting("txt|"):
        parts = key.split("|", 2)
        if len(parts) == 3 and parts[1] >= cutoff:
            texts.append(parts[2])
    return texts[-300:]


def _is_copy_paste(norm, recent_texts):
    if len(norm) < 40:
        return False  # too short to judge safely
    nums = _numbers(norm)
    for old in recent_texts:
        if _numbers(old) != nums:
            continue
        sm = SequenceMatcher(None, norm, old)
        if sm.quick_ratio() >= COPY_SIMILARITY and sm.ratio() >= COPY_SIMILARITY:
            return True
    return False


def _record_text(seen_ids, norm):
    if len(norm) >= 40:
        seen_ids.add(f"txt|{date.today().isoformat()}|{norm}")


def _record_campaign(seen_ids, project, summary):
    clean = (summary or "").replace("|", "/").replace("\n", " ").strip()[:200]
    seen_ids.add(f"alert|{project}|{date.today().isoformat()}|{clean}")


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

    # Read X only since the last successful run (minus 20 min overlap), so
    # nothing is missed between runs and nothing old is re-paid for.
    # Capped at 24h back in case the bot was off for a while.
    import time as _time
    now_ts = int(_time.time())
    last_runs = [int(k.split("|")[1]) for k in seen_ids.keys_starting("lastrun|") if k.split("|")[1].isdigit()]
    since_ts = max(max(last_runs) - 1200, now_ts - 86400) if last_runs else None
    x_posts = scan_x(since_ts)
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

        # Free copy-paste check — near-identical to something already alerted?
        norm = _normalize(post["body"])
        if _is_copy_paste(norm, _recent_texts(seen_ids, PROJECT_DEDUPE_DAYS)):
            print(f"📋 SKIPPED (copy-paste of an alerted post, no AI used): {post['title'][:60]}")
            continue

        # AI verification: confirms it's real + live, picks the tier, writes an English summary.
        verdict = classify_opportunity(post["title"], post["body"], result["tier_hint"])
        if not verdict["verified"]:
            print(f"🤖 AI REJECTED: {post['title'][:60]} — {verdict['reason']}")
            continue

        # Same-campaign dedupe: 10 KOLs shilling one campaign = 1 alert,
        # but a NEW campaign from the same project still alerts.
        project = verdict.get("project") or ""
        if project:
            previous = _recent_campaigns(seen_ids, project, PROJECT_DEDUPE_DAYS)
            if previous and is_same_campaign(project, verdict["summary_en"], post["body"], previous):
                print(f"🔁 SKIPPED (same '{project}' campaign already alerted): {post['title'][:60]}")
                _record_text(seen_ids, norm)   # confirmed repeat — catch its copies for free next time
                continue
            _record_campaign(seen_ids, project, verdict["summary_en"])
        _record_text(seen_ids, norm)

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

    for k in seen_ids.keys_starting("lastrun|"):
        seen_ids._ids.pop(k, None)
    seen_ids.add(f"lastrun|{now_ts}")
    save_seen_posts(seen_ids)
    send_summary(new_alerts, len(all_posts))

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()
