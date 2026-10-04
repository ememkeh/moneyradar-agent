"""
MoneyRadar Agent — Telegram channel scanner
Reads public channels through the t.me/s/<handle> web preview. No login,
no bot token, no API key. Each request returns the channel's ~20 latest posts.

Channels without a public preview (private, or preview disabled) return 0
posts and are skipped — check the per-channel counts in the Actions log.
"""
import html
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from config import TELEGRAM_CHANNELS, TELEGRAM_MAX_AGE_DAYS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_MSG_SPLIT = re.compile(r'<div class="tgme_widget_message_wrap')
_POST_RE = re.compile(r'data-post="([^"]+)"')
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text js-message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
_TEXT_FALLBACK_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
_BR_RE = re.compile(r'<br\s*/?>', re.I)
_TAG_RE = re.compile(r'<[^>]+>')


def _clean(raw_html):
    text = _BR_RE.sub("\n", raw_html)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _parse_time(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def scan_channel(handle):
    """Return a list of post dicts from one public channel."""
    url = f"https://t.me/s/{handle}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        print(f"[telegram] {handle}: request failed ({e})")
        return []

    if resp.status_code != 200:
        print(f"[telegram] {handle}: HTTP {resp.status_code}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=TELEGRAM_MAX_AGE_DAYS)
    posts = []

    for chunk in _MSG_SPLIT.split(resp.text)[1:]:
        post_match = _POST_RE.search(chunk)
        if not post_match:
            continue
        post_ref = post_match.group(1)  # e.g. "Bybit_Announcements/1234"

        text_match = _TEXT_RE.search(chunk) or _TEXT_FALLBACK_RE.search(chunk)
        if not text_match:
            continue  # media-only post
        text = _clean(text_match.group(1))
        if len(text) < 20:
            continue

        time_match = _TIME_RE.search(chunk)
        created_at = time_match.group(1) if time_match else None
        created_dt = _parse_time(created_at)
        if created_dt and created_dt < cutoff:
            continue

        first_line = text.split("\n", 1)[0][:120]
        posts.append({
            "id": f"tg_{post_ref}",
            "title": first_line,
            "body": text,
            "url": f"https://t.me/{post_ref}",
            "author": handle,
            "source": "telegram",
            "created_at": created_at,
        })

    return posts


def scan_telegram_channels():
    """Scan every channel in config.TELEGRAM_CHANNELS. Returns one combined list."""
    all_posts = []
    for handle in TELEGRAM_CHANNELS:
        posts = scan_channel(handle)
        print(f"[telegram] {handle}: {len(posts)} recent posts")
        all_posts.extend(posts)
        time.sleep(1)  # be polite — avoids t.me rate limiting
    return all_posts


if __name__ == "__main__":
    for p in scan_telegram_channels()[:5]:
        print(p["url"], "|", p["title"])
