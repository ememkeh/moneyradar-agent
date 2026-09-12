"""
MoneyRadar Agent — Telegram alert sender
Sends formatted alerts to your Telegram via Bot API. See README.md for bot setup steps.
"""
import os
import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_alert(post, matched_amount):
    """
    Sends a formatted Telegram message for a single qualifying post.
    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[telegram_alert] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — cannot send alert.")
        print(f"[telegram_alert] Would have alerted: {post['title']}")
        return False

    source_label = "🔴 Reddit" if post["source"] == "reddit" else "🐦 X"
    sub_line = f"\n📍 r/{post['subreddit']}" if post.get("subreddit") else ""

    # Plain text, no Markdown — post/tweet content is unpredictable (can contain
    # *, _, [, ] etc.) and Telegram rejects the whole message with a 400 error
    # if Markdown parsing fails on any of it. Plain text can never break.
    message = (
        f"💰 New Referral Opportunity Spotted\n\n"
        f"{source_label}{sub_line}\n"
        f"💵 Matched amount: {matched_amount}\n\n"
        f"{post['title']}\n\n"
        f"🔗 {post['permalink']}"
    )

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
    """Optional: send a short 'scan complete' summary even when nothing qualifies."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    if new_count == 0:
        return  # stay quiet on empty scans — no need to ping you every 6 hours for nothing

    message = f"✅ Scan complete: {new_count} new opportunit{'y' if new_count == 1 else 'ies'} found (of {scanned_count} posts scanned)."
    url = TELEGRAM_API_URL.format(token=token)
    try:
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
    except requests.RequestException:
        pass
