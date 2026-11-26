"""
MoneyRadar Agent — Owner feedback (👍 / 👎 / 🔇 buttons on alerts)

Every alert gets three buttons. The bot only runs every 2 hours, so taps are
collected at the start of the NEXT run (Telegram keeps them for 24h), then:
  👍  -> the alert's summary becomes a "liked" example the AI copies
  👎  -> becomes a "disliked" example the AI avoids; an author with 2+ 👎
         gets skipped before any AI call (free)
  🔇  -> that project never alerts again (checked before AI where possible)

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


def collect_taps(data):
    """Read button taps since the last run and apply them. Returns a count."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        return 0

    res = _tg("getUpdates", token, offset=data["offset"] + 1 if data["offset"] else 0,
              timeout=0, allowed_updates=["callback_query"])
    applied = 0
    for upd in res.get("result", []) or []:
        data["offset"] = max(data["offset"], int(upd.get("update_id", 0)))
        cq = upd.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        # only accept taps from your own chat with the bot
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
            label = "✅ 👍 saved — more like this"
        elif action == "down":
            data["dislikes"].append({"summary": info.get("summary", ""), "date": today})
            author = (info.get("author") or "").lower()
            if author and info.get("source") != "telegram":
                data["bad_authors"][author] = data["bad_authors"].get(author, 0) + 1
            label = "✅ 👎 saved — less like this"
        elif action == "mute":
            project = key  # mute buttons carry the project name
            if project and project not in data["muted"]:
                data["muted"].append(project)
            label = f"✅ 🔇 {project} muted"
        else:
            continue

        applied += 1
        print(f"[feedback] {action}: {info.get('summary') or key}")
        # swap the buttons for a confirmation so you can see it was recorded
        if msg.get("message_id"):
            _tg("editMessageReplyMarkup", token, chat_id=msg["chat"]["id"],
                message_id=msg["message_id"],
                reply_markup={"inline_keyboard": [[{"text": label, "callback_data": "fb|noop|x"}]]})
        _tg("answerCallbackQuery", token, callback_query_id=cq.get("id"))  # may be too old; harmless
    return applied


def buttons(key, project):
    """Inline keyboard for an alert. callback_data must stay under 64 bytes."""
    row = [
        {"text": "👍", "callback_data": f"fb|up|{key}"[:64]},
        {"text": "👎", "callback_data": f"fb|down|{key}"[:64]},
    ]
    if project:
        row.append({"text": f"🔇 mute {project}"[:30], "callback_data": f"fb|mute|{project}"[:64]})
    return {"inline_keyboard": [row]}
