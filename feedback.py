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
    return data


def save(data):
    # keep only the newest alert records
    alerts = data["alerts"]
    if len(alerts) > MAX_ALERTS_KEPT:
        for k in list(alerts)[: len(alerts) - MAX_ALERTS_KEPT]:
            alerts.pop(k, None)
    data["likes"] = data["likes"][-50:]
    data["dislikes"] = data["dislikes"][-50:]
    with open(FEEDBACK_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def remember_alert(data, key, post, project, summary):
    """Store what an alert was about, so a later tap can be matched to it."""
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
    ("pause", "stop alerts (no X or AI spend while paused)"),
    ("resume", "start alerts again, from now"),
    ("stats", "alerts + spend for today, 7 days, this month"),
    ("muted", "list muted projects"),
    ("mute", "mute a project — /mute binance"),
    ("unmute", "unmute a project — /unmute binance"),
    ("blocked", "list authors skipped for 👎"),
    ("unblock", "stop skipping an author — /unblock @name"),
    ("undo", "undo your last tap or command"),
    ("help", "show these commands"),
]
COMMANDS_VERSION = 1


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
        "📊 MoneyRadar stats\n\n"
        f"today: {d1['alerts']} alerts · {d1['runs']} runs · {d1['scanned']} posts scanned · AI ~${d1['ai']:.2f}\n"
        f"7 days: {d7['alerts']} alerts · AI ~${d7['ai']:.2f} · X ~${d7['tweets'] * 0.00015:.2f}\n"
        f"this month: {m_alerts} alerts · AI ~${data['spend'].get(month, 0):.2f} · X ~${m_tweets * 0.00015:.2f}\n\n"
        f"👍 {len(data['likes'])} · 👎 {len(data['dislikes'])} · 🔇 {len(data['muted'])} muted · "
        f"🚫 {sum(1 for c in data['bad_authors'].values() if c >= BAD_AUTHOR_THRESHOLD)} blocked\n"
        f"status: {'⏸️ paused' if data['paused'] else '▶️ running'}"
    )


def _clean_name(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == "_")


def _run_command(data, text):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("help", "start"):
        return "🤖 MoneyRadar controls\n\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS) + \
               "\n\n⏳ commands run at the next scan (within ~2 hours)."
    if cmd == "pause":
        if data["paused"]:
            return "already paused. /resume to start again."
        data["paused"] = True
        _remember(data, {"kind": "pause"})
        return "⏸️ paused. no alerts and no X/AI spend until /resume."
    if cmd == "resume":
        if not data["paused"]:
            return "already running."
        data["paused"] = False
        _remember(data, {"kind": "resume"})
        return "▶️ resumed. alerts start from now (no backlog flood)."
    if cmd == "stats":
        return _stats_text(data)
    if cmd == "muted":
        return ("🔇 muted: " + ", ".join(sorted(data["muted"]))) if data["muted"] else "nothing muted."
    if cmd == "mute":
        p = _clean_name(arg).replace("_", "")
        if not p:
            return "usage: /mute binance"
        if p in data["muted"]:
            return f"{p} is already muted."
        data["muted"].append(p)
        _remember(data, {"kind": "mute", "project": p})
        return f"🔇 {p} muted."
    if cmd == "unmute":
        p = _clean_name(arg).replace("_", "")
        if p not in data["muted"]:
            return f"{p or '(nothing)'} isn't muted. /muted shows the list."
        data["muted"].remove(p)
        _remember(data, {"kind": "unmute", "project": p})
        return f"🔊 {p} unmuted."
    if cmd == "blocked":
        names = sorted(a for a, c in data["bad_authors"].items() if c >= BAD_AUTHOR_THRESHOLD)
        return ("🚫 skipped authors: " + ", ".join("@" + a for a in names)) if names else "no authors blocked."
    if cmd == "unblock":
        a = _clean_name(arg.lstrip("@"))
        if not a or a not in data["bad_authors"]:
            return "usage: /unblock @name — /blocked shows the list."
        _remember(data, {"kind": "unblock", "author": a, "count": data["bad_authors"][a]})
        data["bad_authors"][a] = 0
        return f"✅ @{a} unblocked."
    if cmd == "undo":
        return "↩️ " + _undo(data)
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
            label = "✅ 👍 saved — more like this"
        elif action == "down":
            data["dislikes"].append({"summary": info.get("summary", ""), "date": today})
            author = (info.get("author") or "").lower()
            if author and info.get("source") != "telegram":
                data["bad_authors"][author] = data["bad_authors"].get(author, 0) + 1
            else:
                author = ""
            _remember(data, {"kind": "dislike", "author": author})
            label = "✅ 👎 saved — less like this"
        elif action == "mute":
            project = key  # mute buttons carry the project name
            if project and project not in data["muted"]:
                data["muted"].append(project)
                _remember(data, {"kind": "mute", "project": project})
            label = f"✅ 🔇 {project} muted (/unmute {project} to undo)"
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
        {"text": "👍", "callback_data": f"fb|up|{key}"[:64]},
        {"text": "👎", "callback_data": f"fb|down|{key}"[:64]},
    ]
    if project:
        row.append({"text": f"🔇 mute {project}"[:30], "callback_data": f"fb|mute|{project}"[:64]})
    return {"inline_keyboard": [row]}
