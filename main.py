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
from ai_verify import classify_many, is_same_campaign
import feedback
import link_check
import eligibility
from telegram_alert import send_alert, send_summary, send_text
import ai_verify


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


def _local_now():
    from datetime import datetime, timezone, timedelta as _td
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(getattr(config, "QUIET_TIMEZONE", "Africa/Lagos")))
    except Exception:
        return datetime.now(timezone.utc) + _td(hours=1)   # Lagos = UTC+1


def _in_quiet_hours():
    if not getattr(config, "QUIET_HOURS_ENABLED", False):
        return False
    hour = _local_now().hour
    start, end = getattr(config, "QUIET_START", 23), getattr(config, "QUIET_END", 6)
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


def _quality(post, result, verdict):
    """Score how useful a post is, to pick the best of several versions."""
    score = 0
    author = (post.get("author") or "").lower().replace("_", "")
    project = verdict.get("project") or ""
    if project and author and (project in author or author in project):
        score += 100                      # the project's own account
    level = (post.get("link_report") or {}).get("level")
    score += {"ok": 20, "unknown": 8, "caution": 0, "danger": -60, "skip": -5}.get(level, -5)
    if result.get("matched_amount"):
        score += 10                       # states the reward
    if verdict.get("summary_en"):
        score += 5
    if post.get("watched"):
        score += 5                        # an account you've liked before
    score += min(len(post.get("body") or ""), 1000) // 100   # more detail, up to +10
    return score


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

    # ── your 👍 / 👎 / 🔇 taps and typed commands since last run ──
    fb = feedback.load()
    taps = feedback.collect_taps(fb)
    if taps:
        print(f"[controls] handled {taps} tap(s)/command(s)")

    import time as _time
    if fb["paused"]:
        # Paused with /pause: no X or AI spend. Keep "last run" at now so
        # /resume starts fresh instead of flooding you with the backlog.
        for k in seen_ids.keys_starting("lastrun|") + seen_ids.keys_starting("lastfull|"):
            seen_ids._ids.pop(k, None)
        seen_ids.add(f"lastrun|{int(_time.time())}")
        seen_ids.add(f"lastfull|{int(_time.time())}")
        save_seen_posts(seen_ids)
        feedback.save(fb)
        print("⏸️  Paused via /pause — skipping this scan. Send /resume to start again.")
        return

    # 🌙 Quiet hours: skip the scan entirely (no X/AI spend). "last run" is NOT
    # moved forward, so the first run after QUIET_END catches up on the night.
    if _in_quiet_hours():
        save_seen_posts(seen_ids)
        feedback.save(fb)
        print("🌙 Quiet hours — no scan. Overnight posts will be caught up after "
              f"{getattr(config, 'QUIET_END', 6)}:00.")
        return

    # 🔁 near-misses you rescued from the digest → send them as full alerts
    for key in list(fb["rescue_queue"]):
        nm = fb["near_misses"].get(key)
        fb["rescue_queue"].remove(key)
        if not nm:
            continue
        post = dict(nm["post"])
        post["tier"] = nm.get("tier", "cash")
        post["summary_en"] = nm.get("summary", "")
        post["link_report"] = link_check.check_post(post, fb["domain_cache"])
        if getattr(config, "ELIGIBILITY_CHECK", True):
            try:
                post["eligibility"] = eligibility.check(post, fb["elig_cache"])
            except Exception:
                pass
        if nm.get("project"):
            _record_campaign(seen_ids, nm["project"], nm.get("summary", ""))
        _record_text(seen_ids, _normalize(post.get("body", "")))
        feedback.remember_alert(fb, key, post, nm.get("project", ""), nm.get("summary", ""))
        print(f"🔁 RESCUED → alert: {nm.get('summary', '')[:60]}")
        send_alert(post, nm.get("amount") or None, feedback.buttons(key, nm.get("project", "")))

    reddit_posts = []  # Reddit disabled — no API access

    # Read X only since the last successful run (minus overlap), so nothing is
    # missed between runs and nothing old is re-paid for. Capped at 24h back.
    #   lastrun  = any run (fast lane accounts read since then)
    #   lastfull = last full keyword sweep
    now_ts = int(_time.time())
    def _last(prefix):
        vals = [int(k.split("|")[1]) for k in seen_ids.keys_starting(prefix) if k.split("|")[1].isdigit()]
        return max(vals) if vals else None
    last_any, last_full = _last("lastrun|"), _last("lastfull|")
    full_every = getattr(config, "FULL_SWEEP_EVERY_MINUTES", 120) * 60
    # 10-min slack so GitHub's cron delays don't push a sweep back a whole slot
    full_sweep = last_full is None or now_ts - last_full >= full_every - 600
    since_full = max(last_full - 1200, now_ts - 86400) if last_full else (
        max(last_any - 1200, now_ts - 86400) if last_any else None)
    since_any = max(last_any - 600, now_ts - 86400) if last_any else since_full
    print("🔎 FULL SWEEP (keywords + accounts + Telegram)" if full_sweep else "⚡ FAST LANE (watched + official accounts only)")

    # Morning catch-up: a long gap (e.g. after quiet hours) gets more result
    # pages per search, so a busy night isn't cut off at the usual page limit.
    gap_hours = (now_ts - since_full) / 3600 if since_full else 0
    pages = None
    if full_sweep and gap_hours >= 4:
        pages = min(10, getattr(config, "X_MAX_PAGES", 3) * int(gap_hours // 2 + 1))
        msg = (("☀️ morning catch-up" if _local_now().hour < 10 else "🔄 catch-up")
               + f" — scanning the last ~{gap_hours:.0f}h of posts")
        print(msg)
        send_text(msg)

    for msg in feedback.update_watchlist(fb):
        print(f"[sources] {msg}")
        send_text(msg)
    x_posts = scan_x(since_full, fb["watched"], keywords=full_sweep, accounts_since=since_any,
                     official=getattr(config, "X_OFFICIAL_ACCOUNTS", []), max_pages=pages)
    if not full_sweep:
        for p in x_posts:
            p["fast"] = True
    print(f"Fetched {len(x_posts)} posts from X.")

    telegram_posts = []
    if TELEGRAM_ENABLED and full_sweep:
        try:
            telegram_posts = scan_telegram_channels()
        except Exception as e:
            # A Telegram failure should never stop X alerts from going out
            print(f"[telegram] scanner failed, skipping this run: {e}")
    print(f"Fetched {len(telegram_posts)} posts from Telegram channels.")

    all_posts = reddit_posts + x_posts + telegram_posts
    new_alerts = 0
    held_back = []
    today_str = date.today().isoformat()

    ai_verify.set_feedback_examples(feedback.liked_examples(fb), feedback.disliked_examples(fb))

    # ── PHASE 1: free checks (no AI) ──
    candidates = []
    for post in all_posts:
        if post["id"] in seen_ids:
            continue  # already handled

        seen_ids.add(post["id"])  # mark as seen regardless of pass/fail — don't re-evaluate it next run

        result = evaluate_post(post["title"], post["body"])
        if not result["passes"]:
            continue  # silently skip — no need to log every non-match

        # Same-day author dedupe — X only. Checked here, but only recorded
        # after the AI approves, so a rejected post doesn't block that
        # author's genuine offer later the same day.
        author = post.get("author")
        author_key = None
        # (watched accounts are exempt: a 2nd, different offer from them the same
        #  day still comes through — repeats are caught by the campaign check)
        if author and post.get("source") != "telegram" and not post.get("watched"):
            if feedback.is_bad_author(fb, author):
                print(f"👎 SKIPPED (author you downvoted 2+ times, no AI used): @{author}")
                continue
            author_key = f"authorday_{author}_{today_str}"
            if author_key in seen_ids:
                print(f"⏭️  SKIPPED (already alerted this author today): {post['title'][:60]}")
                continue

        muted = feedback.muted_project_in_text(fb, post["body"])
        if muted:
            print(f"🔇 SKIPPED (muted project '{muted}', no AI used): {post['title'][:60]}")
            continue

        # Free copy-paste check — near-identical to something already alerted?
        norm = _normalize(post["body"])
        if _is_copy_paste(norm, _recent_texts(seen_ids, PROJECT_DEDUPE_DAYS)):
            print(f"📋 SKIPPED (copy-paste of an alerted post, no AI used): {post['title'][:60]}")
            continue

        candidates.append((post, result, author_key, norm))

    # ── PHASE 2: AI check, 10 posts per call ──
    if candidates:
        print(f"🤖 AI checking {len(candidates)} post(s) in batches of {ai_verify.BATCH_SIZE}")
    verdicts = classify_many([(p["title"], p["body"], r["tier_hint"]) for p, r, _, _ in candidates])

    # ── PHASE 3a: keep AI-approved posts, run the link check, score them ──
    approved = []
    for (post, result, author_key, norm), verdict in zip(candidates, verdicts):
        if not verdict["verified"]:
            if ai_verify.AI_DOWN["reason"]:
                # Not a real rejection — hold it so it's checked again once AI is back
                held_back.append(post["id"])
                continue
            if verdict.get("close"):
                feedback.add_near_miss(fb, post["id"][-40:], post, verdict, result["matched_amount"])
                print(f"🤏 NEAR MISS (saved for digest): {post['title'][:60]} — {verdict['reason']}")
            else:
                print(f"🤖 AI REJECTED: {post['title'][:60]} — {verdict['reason']}")
            continue
        post["link_report"] = link_check.check_post(post, fb["domain_cache"])
        approved.append((post, result, author_key, norm, verdict))

    # Best version first: when several creators post the same campaign in one
    # scan, the clearest one is alerted and the rest are skipped as repeats.
    approved.sort(key=lambda x: _quality(x[0], x[1], x[4]), reverse=True)

    # ── PHASE 3b: dedupe + alert ──
    for post, result, author_key, norm, verdict in approved:
        # re-check things that may have changed earlier in this same run
        if author_key and author_key in seen_ids:
            print(f"⏭️  SKIPPED (already alerted this author today): {post['title'][:60]}")
            continue
        if _is_copy_paste(norm, _recent_texts(seen_ids, PROJECT_DEDUPE_DAYS)):
            print(f"📋 SKIPPED (copy-paste of a post alerted this run): {post['title'][:60]}")
            continue

        project = verdict.get("project") or ""
        if project and project in fb["muted"]:
            print(f"🔇 SKIPPED (muted project '{project}'): {post['title'][:60]}")
            continue

        # Same-campaign dedupe: 10 KOLs shilling one campaign = 1 alert,
        # but a NEW campaign from the same project still alerts.
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

        # 🛡️ link safety check (done in phase 3a)
        report = post["link_report"]
        if report["level"] == "danger":
            print(f"🚨 risky link: {report['lines']}")
            if getattr(config, "BLOCK_DANGER", False):
                print(f"🚫 BLOCKED (risky link, BLOCK_DANGER on): {post['title'][:60]}")
                continue

        # Nigeria eligibility (free checks first; one tiny AI call only if terms were found)
        if getattr(config, "ELIGIBILITY_CHECK", True):
            try:
                elig = eligibility.check(post, fb["elig_cache"])
            except Exception as e:      # never let this stop an alert
                print(f"[eligibility] failed: {e}")
                elig = {"status": "unclear", "line": "NIGERIA ELIGIBILITY: UNCLEAR — check failed"}
            post["eligibility"] = elig
            print(f"🇳🇬 {elig['line']}")
            if elig["status"] == "no" and getattr(config, "HIDE_NOT_ELIGIBLE", False):
                print(f"🚫 HIDDEN (not available in Nigeria): {post['title'][:60]}")
                continue

        fb_key = post["id"][-40:]
        feedback.remember_alert(fb, fb_key, post, project, verdict["summary_en"])

        print(f"✅ MATCH [{verdict['tier']}]: {post['title'][:80]} ({result['matched_amount']})")
        success = send_alert(post, result["matched_amount"], feedback.buttons(fb_key, project))
        if success:
            new_alerts += 1

    # ── spend this run ──
    ai_cost = ai_verify.usage_cost()
    month_cost = feedback.add_spend(fb, ai_cost)
    x_cost = len(x_posts) * 0.00015   # TwitterAPI.io ≈ $0.15 per 1,000 tweets
    spend_line = (f"Cost this scan: AI {ai_verify.USAGE['calls']} call(s) ~${ai_cost:.3f} "
                  f"(this month ~${month_cost:.2f}) · X {len(x_posts)} tweets ~${x_cost:.3f}")
    print(spend_line)
    feedback.record_run(fb, new_alerts, len(all_posts), ai_cost, len(x_posts))
    feedback.save(fb)

    if ai_verify.AI_DOWN["reason"]:
        # AI was down: forget held-back posts and keep the old "last run" time,
        # so the first run after you top up re-reads them (up to 24h back).
        for pid in held_back:
            seen_ids._ids.pop(pid, None)
        print(f"⏸️  AI unavailable — {len(held_back)} post(s) held for the next run")
    else:
        for k in seen_ids.keys_starting("lastrun|"):
            seen_ids._ids.pop(k, None)
        seen_ids.add(f"lastrun|{now_ts}")
        if full_sweep:
            for k in seen_ids.keys_starting("lastfull|"):
                seen_ids._ids.pop(k, None)
            seen_ids.add(f"lastfull|{now_ts}")
    save_seen_posts(seen_ids)
    if full_sweep:
        # one summary per full sweep — fast-lane runs only send the alerts themselves
        send_summary(new_alerts, len(all_posts), spend_line)

    # 🔁 daily near-miss digest (or on demand via /nearmiss)
    today_local = _local_now().date().isoformat()
    due = (getattr(config, "DIGEST_ENABLED", True)
           and _local_now().hour >= getattr(config, "DIGEST_HOUR", 20)
           and fb["digest_sent"] != today_local)
    if due or fb["digest_now"]:
        text, markup = feedback.build_digest(fb)
        if text:
            send_text(text, markup)
            print(f"🔁 near-miss digest sent ({len(markup['inline_keyboard'])} button row(s))")
        if due:
            fb["digest_sent"] = today_local
        fb["digest_now"] = False
        feedback.save(fb)

    # AI down (credits empty / bad key): one Telegram warning per day, no spam
    if ai_verify.AI_DOWN["reason"]:
        warn_key = f"aidown_warned_{date.today().isoformat()}"
        if warn_key not in seen_ids:
            send_text(
                "⚠️ MoneyRadar paused alerts — the Claude AI check isn't working.\n\n"
                f"Reason: {ai_verify.AI_DOWN['reason'][:200]}\n\n"
                "Most likely the Anthropic credits ran out. Top up at "
                "platform.claude.com → Billing. Alerts resume on the next run after that."
            )
            seen_ids.add(warn_key)
            save_seen_posts(seen_ids)

    print(f"=== Scan complete. {new_alerts} new alert(s) sent out of {len(all_posts)} posts scanned. ===")


if __name__ == "__main__":
    run_scan()
