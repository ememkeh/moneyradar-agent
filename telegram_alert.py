"""
MoneyRadar Agent — Telegram alert sender
Sends formatted alerts to your Telegram via Bot API. See README.md for bot setup steps.

Alert layout:
  🪂 TASK AIRDROP
  🐦 X · @author · 🇨🇳 chinese
  💵 $10.00            (only when an amount was found)

  📝 one-line english summary from the AI check

  original post text (trimmed)

  🔗 link
"""
import os
import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

TIER_HEADERS = {
    "cash": "💰 CASH OFFER",
    "airdrop": "🪂 TASK AIRDROP",
    "points": "🎯 POINTS → CASH",
}

LANG_LABELS = {
    "zh": "🇨🇳 chinese",
    "ko": "🇰🇷 korean",
}

MAX_ORIGINAL_CHARS = 600   # keeps alerts readable; full post is one tap away


def _source_line(post):
    source = post.get("source")
    author = post.get("author") or ""
    if source == "telegram":
        line = f"✈️ Telegram · @{author}" if author else "✈️ Telegram"
    elif source == "reddit":
        line = f"🔴 Reddit · r/{post['subreddit']}" if post.get("subreddit") else "🔴 Reddit"
    else:
        line = f"🐦 X · @{author}" if author else "🐦 X"

    lang_label = LANG_LABELS.get(post.get("lang"))
    if lang_label:
        line += f" · {lang_label}"
    return line


def _post_link(post):
    return post.get("permalink") or post.get("url") or ""


def send_alert(post, matched_amount):
    """
    Sends a formatted Telegram message for a single qualifying post.
    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[telegram_alert] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — cannot send alert.")
        print(f"[telegram_alert] Would have alerted: {post.get('title', '')}")
        return False

    header = TIER_HEADERS.get(post.get("tier"), "💰 NEW OPPORTUNITY")

    lines = [header, _source_line(post)]
    if matched_amount:
        lines.append(f"💵 {matched_amount}")

    summary = (post.get("summary_en") or "").strip()
    if summary:
        lines.append("")
        lines.append(f"📝 {summary}")

    original = (post.get("body") or post.get("title") or "").strip()
    if len(original) > MAX_ORIGINAL_CHARS:
        original = original[:MAX_ORIGINAL_CHARS].rstrip() + "…"
    if original:
        lines.append("")
        lines.append(original)

    link = _post_link(post)
    if link:
        lines.append("")
        lines.append(f"🔗 {link}")

    # Plain text, no Markdown — post/tweet content is unpredictable (can contain
    # *, _, [, ] etc.) and Telegram rejects the whole message with a 400 error
    # if Markdown parsing fails on any of it. Plain text can never break.
    message = "\n".join(lines)[:4000]  # Telegram's hard limit is 4096

    url = TELEGRAM_API_URL.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[telegram_alert] Failed to send alert: {e}")
        return False


def send_summary(new_count, scanned_count):
    """Send a short 'scan complete' summary — only when something was found."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    if new_count == 0:
        return  # stay quiet on empty scans

    message = f"✅ Scan complete: {new_count} new opportunit{'y' if new_count == 1 else 'ies'} found (of {scanned_count} posts scanned)."
    url = TELEGRAM_API_URL.format(token=token)
    try:
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
    except requests.RequestException:
        pass
