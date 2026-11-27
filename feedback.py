"""
MoneyRadar Agent — Owner feedback (👍 / 👎 / 🔇 buttons on alerts)

Every alert gets three buttons. The bot only runs every 2 hours, so taps are
collected at the start of the NEXT run (Telegram keeps them for 24h), then:
  👍  -> the alert's summary becomes a "liked" example the AI copies
  👎  -> becomes a "disliked" example the AI avoids; an author with 2+ 👎
         gets skipped before any AI call (free)
  🔇  -> that project never alerts again (checked before AI where possible)

You can also type commands to the bot: /pause /resume /stats /muted /mute
/unmute /blocked /unblock /undo /help (they run at the next scan).

Everything is saved in feedback.json (committed back to the repo by scan.yml),
kept separate from seen_posts.json so mutes and examples never get trimmed.
"""
import json
import os
from datetime import date

import requests

FEEDBACK_FILE = "feedback.json"
TG_API = "https://api.telegram.org/bot{token}/{method}"

MAX_EXAMPLES = 8          # liked/disliked examples shown to the AI
BAD_AUTHOR_THRESHOLD = 2  # 👎 count before an author is skipped
MAX_ALERTS_KEPT = 400     # alert details remembered so taps can be matched

# ── SOURCE AUTO-DISCOVERY ──
WATCH_AFTER_LIKES = 3     # 👍 on an account's alerts before it's watched directly
UNWATCH_AFTER_DISLIKES = 2
UNWATCH_IF_QUIET_DAYS = 30
MAX_WATCHED = 40


def load():
    data = {}
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            print(f"[feedback] could not read {FEEDBACK_FILE} ({e}) — starting fresh")
    data.setdefault("offset", 0)
    data.setdefault("alerts", {})
    data.setdefault("likes", [])
    data.setdefault("dislikes", [])
    data.setdefault("bad_authors", {})
    data.setdefault("muted", [])
    data.setdefault("spend", {})
    data.setdefault("stats", {})
    data.setdefault("history", [])
    data.setdefault("paused", False)
    data.setdefault("domain_cache", {})
    data.setdefault("author_hits", {})   # per X author: 👍 / 👎 / alerts
    data.setdefault("watched", [])       # auto-discovered (or /watch) accounts
    data.setdefault("unwatched", [])     # accounts you removed — never auto-added again
    data.setdefault("near_misses", {})   # AI-rejected but close — for the daily digest
    data.setdefault("rescue_queue", [])  # 🔁 taps waiting to be turned into alerts
    data.setdefault("digest_sent", "")   # date of the last near-miss digest
    data.setdefault("digest_now", False) # /nearmiss asked for one at the next run
    data.setdefault("elig_cache", {})    # per-site Nigeria eligibility answers
    return data


def save(data):
    # keep only the newest alert records
    alerts = data["alerts"]
    if len(alerts) > MAX_ALERTS_KEPT:
        for k in list(alerts)[: len(alerts) - MAX_ALERTS_KEPT]:
            alerts.pop(k, None)
    # near-misses: keep 3 days (so 🔁 taps on yesterday's digest still work)
    cutoff = date.fromordinal(date.today().toordinal() - 3).isoformat()
    for k in [k for k, v in data["near_misses"].items() if v.get("date", "") < cutoff]:
        data["near_misses"].pop(k, None)
    if len(data["elig_cache"]) > 800:
        for k in list(data["elig_cache"])[:-800]:
            data["elig_cache"].pop(k, None)
    if len(data["domain_cache"]) > 1500:
        for k in list(data["domain_cache"])[:-1500]:
            data["domain_cache"].pop(k, None)
    data["likes"] = data["likes"][-50:]
    data["dislikes"] = data["dislikes"][-50:]
    with open(FEEDBACK_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _hit(data, info, field):
    # count each alert once, even if tapped again
    if info.get("voted") == field:
        return
    info["voted"] = field
    a = (info.get("author") or "").lower()
    if a and info.get("source") == "x":
        h = data["author_hits"].setdefault(a, {"up": 0, "down": 0, "alerts": 0, "last": ""})
        h[field] = h.get(field, 0) + 1


def update_watchlist(data):
    """
    Promote accounts whose alerts you keep 👍-ing; drop ones you 👎 or that
    went quiet. Returns a list of human-readable change messages.
    """
    changes = []
    today = date.today()
    for a, h in data["author_hits"].items():
        if (a not in data["watched"] and a not in data["unwatched"]
                and h.get("up", 0) >= WATCH_AFTER_LIKES and h.get("down", 0) == 0
                and len(data["watched"]) < MAX_WATCHED):
            data["watched"].append(a)
            changes.append(f"NEW SOURCE: now watching @{a} directly ({h['up']} good alerts, 0 bad). Send /unwatch @{a} to stop.")
    for a in list(data["watched"]):
        h = data["author_hits"].get(a, {})
        if h.get("down", 0) >= UNWATCH_AFTER_DISLIKES:
            data["watched"].remove(a)
            changes.append(f"SOURCE REMOVED: stopped watching @{a} ({h['down']} bad alerts).")
            continue
        last = h.get("last") or ""
        if last and (today - date.fromisoformat(last)).days > UNWATCH_IF_QUIET_DAYS:
            data["watched"].remove(a)
            changes.append(f"SOURCE REMOVED: stopped watching @{a} (no alerts in {UNWATCH_IF_QUIET_DAYS} days).")
    return changes


# ── 🔁 NEAR-MISS DIGEST ──
DIGEST_MAX_ITEMS = 15


def add_near_miss(data, key, post, verdict, matched_amount):
    """Store an AI-rejected-but-close post so it can show up in the daily digest."""
    data["near_misses"][key] = {
        "date": date.today().isoformat(),
        "summary": (verdict.get("summary_en") or post.get("title") or "")[:200],
        "reason": (verdict.get("reason") or "")[:140],
        "project": verdict.get("project") or "",
        "tier": verdict.get("tier_guess") or "cash",
        "amount": matched_amount or "",
        "post": {k: post.get(k) for k in ("id", "title", "body", "url", "permalink",
                                          "author", "source", "lang", "links")},
        "in_digest": False,
        "rescued": False,
    }


def digest_items(data):
    """Near-misses not yet shown in a digest, newest first."""
    items = [(k, v) for k, v in data["near_misses"].items()
             if not v.get("in_digest") and not v.get("rescued")]
    return items[-DIGEST_MAX_ITEMS:][::-1]


def build_digest(data):
    """Returns (text, reply_markup) or (None, None) if there's nothing to show."""
    items = digest_items(data)
    if not items:
        return None, None
    lines = [f"NEAR-MISS DIGEST — {len(items)} post(s) the AI almost rejected",
             "Tap \"Rescue\" + the number to get that post as a full alert. "
             "Rescuing also teaches the AI you wanted it.", ""]
    buttons_row, keyboard = [], []
    for n, (k, v) in enumerate(items, 1):
        link = (v["post"].get("permalink") or v["post"].get("url") or "")
        amount = f" · {v['amount']}" if v.get("amount") else ""
        lines.append(f"{n}. {v['summary'] or '(no summary)'}{amount}")
        lines.append(f"   Rejected because: {v['reason'] or 'unclear'}")
        if link:
            lines.append(f"   Link: {link}")
        lines.append("")
        v["in_digest"] = True
        buttons_row.append({"text": f"Rescue {n}", "callback_data": f"fb|rescue|{k}"[:64]})
        if len(buttons_row) == 3:
            keyboard.append(buttons_row)
            buttons_row = []
    if buttons_row:
        keyboard.append(buttons_row)
    return "\n".join(lines)[:4000], {"inline_keyboard": keyboard}


def remember_alert(data, key, post, project, summary):
    """Store what an alert was about, so a later tap can be matched to it."""
    a = (post.get("author") or "").lower()
    if a and post.get("source") == "x":
        h = data["author_hits"].setdefault(a, {"up": 0, "down": 0, "alerts": 0, "last": ""})
        h["alerts"] = h.get("alerts", 0) + 1
        h["last"] = date.today().isoformat()
    data["alerts"][key] = {
        "author": post.get("author") or "",
        "source": post.get("source") or "",
        "project": project or "",
        "summary": (summary or post.get("title") or "")[:200],
        "date": date.today().isoformat(),
    }


def liked_examples(data):
    return [x["summary"] for x in data["likes"][-MAX_EXAMPLES:] if x.get("summary")]


def disliked_examples(data):
    return [x["summary"] for x in data["dislikes"][-MAX_EXAMPLES:] if x.get("summary")]


def is_bad_author(data, author):
    return bool(author) and data["bad_authors"].get(author.lower(), 0) >= BAD_AUTHOR_THRESHOLD


def muted_project_in_text(data, text):
    """Free pre-AI check: does the post clearly name a muted project?"""
    squashed = "".join(ch for ch in (text or "").lower() if ch.isalnum())
    for p in data["muted"]:
        if len(p) >= 4 and p in squashed:
            return p
    return None


def add_spend(data, cost):
    month = date.today().strftime("%Y-%m")
    data["spend"][month] = round(data["spend"].get(month, 0.0) + cost, 6)
    return data["spend"][month]


def _tg(method, token, **payload):
    try:
        r = requests.post(TG_API.format(token=token, method=method), json=payload, timeout=15)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[feedback] telegram {method} failed: {e}")
        return {}


# ── TELEGRAM CONTROLS ──
# Commands you type to the bot. Like button taps, they're read at the start of
# the next scan (every 2 hours), and the bot replies then.
COMMANDS = [
    ("pause", "stop all alerts until /resume (no X or AI spend)"),
    ("resume", "start alerts again, from now"),
    ("stats", "alerts and spend: today, 7 days, this month"),
    ("muted", "list muted projects"),
    ("mute", "mute a project, e.g. /mute binance"),
    ("unmute", "unmute a project, e.g. /unmute binance"),
    ("blocked", "list authors skipped after bad-alert taps"),
    ("unblock", "stop skipping an author, e.g. /unblock @name"),
    ("sources", "list X accounts watched directly"),
    ("watch", "watch an X account directly, e.g. /watch @name"),
    ("unwatch", "stop watching an account, e.g. /unwatch @name"),
    ("nearmiss", "send the near-miss digest at the next run"),
    ("undo", "undo your last button tap or command"),
    ("help", "show all commands"),
]
COMMANDS_VERSION = 4


def _remember(data, entry):
    data["history"].append(entry)
    data["history"] = data["history"][-30:]


def _undo(data):
    if not data["history"]:
        return "nothing to undo."
    last = data["history"].pop()
    kind = last.get("kind")
    if kind == "like" and data["likes"]:
        data["likes"].pop()
        return "undone: last 👍"
    if kind == "dislike":
        if data["dislikes"]:
            data["dislikes"].pop()
        a = last.get("author")
        if a and data["bad_authors"].get(a, 0) > 0:
            data["bad_authors"][a] -= 1
        return "undone: last 👎"
    if kind == "mute":
        p = last.get("project")
        if p in data["muted"]:
            data["muted"].remove(p)
        return f"undone: {p} is unmuted"
    if kind == "unmute":
        p = last.get("project")
        if p and p not in data["muted"]:
            data["muted"].append(p)
        return f"undone: {p} is muted again"
    if kind == "unblock":
        data["bad_authors"][last["author"]] = last.get("count", 0)
        return f"undone: @{last['author']} is blocked again"
    if kind == "watch":
        a = last.get("author")
        if a in data["watched"]:
            data["watched"].remove(a)
        return f"undone: stopped watching @{a}"
    if kind == "unwatch":
        a = last.get("author")
        if a and a not in data["watched"]:
            data["watched"].append(a)
        if a in data["unwatched"]:
            data["unwatched"].remove(a)
        return f"undone: watching @{a} again"
    if kind == "pause":
        data["paused"] = False
        return "undone: alerts resumed"
    if kind == "resume":
        data["paused"] = True
        return "undone: alerts paused again"
    return "nothing to undo."


def _stats_text(data):
    from datetime import timedelta
    stats = data["stats"]
    today = date.today()
    def total(days):
        keys = {(today - timedelta(days=d)).isoformat() for d in range(days)}
        t = {"alerts": 0, "scanned": 0, "runs": 0, "ai": 0.0, "tweets": 0}
        for k, v in stats.items():
            if k in keys:
                for f in t:
                    t[f] += v.get(f, 0)
        return t
    d1, d7 = total(1), total(7)
    month = today.strftime("%Y-%m")
    m_alerts = sum(v.get("alerts", 0) for k, v in stats.items() if k.startswith(month))
    m_tweets = sum(v.get("tweets", 0) for k, v in stats.items() if k.startswith(month))
    return (
        "MONEYRADAR STATS\n\n"
        f"today: {d1['alerts']} alerts · {d1['runs']} runs · {d1['scanned']} posts scanned · AI ~${d1['ai']:.2f}\n"
        f"7 days: {d7['alerts']} alerts · AI ~${d7['ai']:.2f} · X ~${d7['tweets'] * 0.00015:.2f}\n"
        f"this month: {m_alerts} alerts · AI ~${data['spend'].get(month, 0):.2f} · X ~${m_tweets * 0.00015:.2f}\n\n"
        f"Good alerts: {len(data['likes'])} · Bad alerts: {len(data['dislikes'])} · "
        f"Muted projects: {len(data['muted'])} · "
        f"Blocked authors: {sum(1 for c in data['bad_authors'].values() if c >= BAD_AUTHOR_THRESHOLD)}\n"
        f"Status: {'PAUSED' if data['paused'] else 'RUNNING'}"
    )


def _clean_name(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == "_")


def _run_command(data, text):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("help", "start"):
        return "MONEYRADAR COMMANDS\n\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS) + \
               "\n\nCommands take effect at the next run (within ~30 minutes)."
    if cmd == "pause":
        if data["paused"]:
            return "already paused. /resume to start again."
        data["paused"] = True
        _remember(data, {"kind": "pause"})
        return "PAUSED. No alerts and no X/AI spend until you send /resume."
    if cmd == "resume":
        if not data["paused"]:
            return "already running."
        data["paused"] = False
        _remember(data, {"kind": "resume"})
        return "RESUMED. Alerts start again from now (no backlog flood)."
    if cmd == "stats":
        return _stats_text(data)
    if cmd == "muted":
        return ("Muted projects: " + ", ".join(sorted(data["muted"]))) if data["muted"] else "Nothing muted."
    if cmd == "mute":
        p = _clean_name(arg).replace("_", "")
        if not p:
            return "usage: /mute binance"
        if p in data["muted"]:
            return f"{p} is already muted."
        data["muted"].append(p)
        _remember(data, {"kind": "mute", "project": p})
        return f"Muted {p}. Use /unmute {p} to undo."
    if cmd == "unmute":
        p = _clean_name(arg).replace("_", "")
        if p not in data["muted"]:
            return f"{p or '(nothing)'} isn't muted. /muted shows the list."
        data["muted"].remove(p)
        _remember(data, {"kind": "unmute", "project": p})
        return f"Unmuted {p}."
    if cmd == "blocked":
        names = sorted(a for a, c in data["bad_authors"].items() if c >= BAD_AUTHOR_THRESHOLD)
        return ("Blocked authors (skipped for bad alerts): " + ", ".join("@" + a for a in names)) if names else "no authors blocked."
    if cmd == "unblock":
        a = _clean_name(arg.lstrip("@"))
        if not a or a not in data["bad_authors"]:
            return "usage: /unblock @name — /blocked shows the list."
        _remember(data, {"kind": "unblock", "author": a, "count": data["bad_authors"][a]})
        data["bad_authors"][a] = 0
        return f"Unblocked @{a}."
    if cmd == "sources":
        if not data["watched"]:
            return ("no accounts watched yet. accounts get added automatically after "
                    f"{WATCH_AFTER_LIKES} 👍 on their alerts, or use /watch @name.")
        rows = []
        for a in data["watched"]:
            h = data["author_hits"].get(a, {})
            rows.append(f"@{a} — {h.get('alerts', 0)} alerts · {h.get('up', 0)} good · {h.get('down', 0)} bad")
        return "Watched accounts:\n" + "\n".join(rows)
    if cmd == "watch":
        a = _clean_name(arg.lstrip("@"))
        if not a:
            return "usage: /watch @name"
        if a in data["watched"]:
            return f"already watching @{a}."
        data["watched"].append(a)
        if a in data["unwatched"]:
            data["unwatched"].remove(a)
        _remember(data, {"kind": "watch", "author": a})
        return f"Now watching @{a}. Their posts still go through the AI check."
    if cmd == "unwatch":
        a = _clean_name(arg.lstrip("@"))
        if a not in data["watched"]:
            return f"not watching @{a or '?'}. /sources shows the list."
        data["watched"].remove(a)
        if a not in data["unwatched"]:
            data["unwatched"].append(a)
        _remember(data, {"kind": "unwatch", "author": a})
        return f"Stopped watching @{a}. They won't be auto-added again."
    if cmd == "nearmiss":
        n = len(digest_items(data))
        if not n:
            return "no new near-misses right now."
        data["digest_now"] = True
        return f"{n} near-miss(es) waiting — sending the near-miss digest now."
    if cmd == "undo":
        return "Undo: " + _undo(data)
    return "unknown command. /help shows what I can do."


def collect_taps(data):
    """
    Read button taps AND typed commands since the last run and apply them.
    Returns the number of taps/commands handled.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        return 0

    # register the "/" command menu in Telegram once
    if data.get("commands_version") != COMMANDS_VERSION:
        r = _tg("setMyCommands", token,
                commands=[{"command": c, "description": d[:60]} for c, d in COMMANDS])
        if r.get("ok"):
            data["commands_version"] = COMMANDS_VERSION

    res = _tg("getUpdates", token, offset=data["offset"] + 1 if data["offset"] else 0,
              timeout=0, allowed_updates=["callback_query", "message"])
    applied = 0
    for upd in res.get("result", []) or []:
        data["offset"] = max(data["offset"], int(upd.get("update_id", 0)))

        # ── typed commands ──
        m = upd.get("message")
        if m:
            if str((m.get("chat") or {}).get("id", "")) != chat_id:
                continue  # only you can control the bot
            text = m.get("text") or ""
            if text.startswith("/"):
                reply = _run_command(data, text)
                print(f"[controls] {text.split()[0]} -> {reply.splitlines()[0]}")
                _tg("sendMessage", token, chat_id=chat_id, text=reply,
                    reply_to_message_id=m.get("message_id"))
                applied += 1
            continue

        # ── 👍 / 👎 / 🔇 taps ──
        cq = upd.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != chat_id:
            continue

        parts = (cq.get("data") or "").split("|", 2)
        if len(parts) != 3 or parts[0] != "fb":
            continue
        action, key = parts[1], parts[2]
        info = data["alerts"].get(key, {})
        today = date.today().isoformat()

        if action == "up":
            data["likes"].append({"summary": info.get("summary", ""), "date": today})
            _remember(data, {"kind": "like"})
            _hit(data, info, "up")
            label = "Saved: GOOD alert — more like this"
        elif action == "down":
            data["dislikes"].append({"summary": info.get("summary", ""), "date": today})
            author = (info.get("author") or "").lower()
            if author and info.get("source") != "telegram":
                data["bad_authors"][author] = data["bad_authors"].get(author, 0) + 1
            else:
                author = ""
            _remember(data, {"kind": "dislike", "author": author})
            _hit(data, info, "down")
            label = "Saved: BAD alert — less like this"
        elif action == "rescue":
            nm = data["near_misses"].get(key)
            if not nm:
                label = "Too old to rescue (kept 3 days)"
            elif nm.get("rescued"):
                label = "Already rescued"
            else:
                nm["rescued"] = True
                data["rescue_queue"].append(key)
                # the AI was wrong here — teach it
                data["likes"].append({"summary": nm.get("summary", ""), "date": today})
                _remember(data, {"kind": "like"})
                label = "Rescued — full alert coming at the next run"
            applied += 1
            print(f"[feedback] rescue: {key}")
            _tg("answerCallbackQuery", token, callback_query_id=cq.get("id"), text=label[:60])
            continue
        elif action == "mute":
            project = key  # mute buttons carry the project name
            if project and project not in data["muted"]:
                data["muted"].append(project)
                _remember(data, {"kind": "mute", "project": project})
            label = f"Muted {project} (/unmute {project} to undo)"
        else:
            continue

        applied += 1
        print(f"[feedback] {action}: {info.get('summary') or key}")
        # swap the buttons for a confirmation so you can see it was recorded
        if msg.get("message_id"):
            _tg("editMessageReplyMarkup", token, chat_id=msg["chat"]["id"],
                message_id=msg["message_id"],
                reply_markup={"inline_keyboard": [[{"text": label[:60], "callback_data": "fb|noop|x"}]]})
        _tg("answerCallbackQuery", token, callback_query_id=cq.get("id"))  # may be too old; harmless
    return applied


def record_run(data, alerts, scanned, ai_cost, tweets):
    """Per-day counters for /stats."""
    day = date.today().isoformat()
    s = data["stats"].setdefault(day, {"alerts": 0, "scanned": 0, "runs": 0, "ai": 0.0, "tweets": 0})
    s["alerts"] += alerts
    s["scanned"] += scanned
    s["runs"] += 1
    s["ai"] = round(s["ai"] + ai_cost, 6)
    s["tweets"] += tweets
    # keep ~2 months of daily stats
    for k in sorted(data["stats"])[:-62]:
        data["stats"].pop(k, None)


def buttons(key, project):
    """Inline keyboard for an alert. callback_data must stay under 64 bytes."""
    row = [
        {"text": "Good alert", "callback_data": f"fb|up|{key}"[:64]},
        {"text": "Bad alert", "callback_data": f"fb|down|{key}"[:64]},
    ]
    if project:
        row.append({"text": f"Mute {project}"[:30], "callback_data": f"fb|mute|{project}"[:64]})
    return {"inline_keyboard": [row]}
