"""
MoneyRadar Agent — Link safety check
Runs only on posts that are about to be ALERTED (so a few lookups per run, free).
For each link in the post it:
  1. follows short links (t.co, bit.ly, onelink.me ...) to the real destination
  2. flags lookalikes of big brands (b1nance.com, bybit-claim.xyz, xn-- punycode)
  3. looks up how old the domain is (free RDAP lookup, no API key)
  4. marks known official domains as ✅

Each link in the alert gets a plain-words label:
  ✅ OFFICIAL SITE                   a known brand's real domain
  ✅ LOOKS LEGIT (established site)  domain registered 90+ days ago
  ⚪ CAN'T VERIFY (age hidden)       age lookup not available (e.g. some .ng)
  🟡 CAUTION — VERY NEW SITE         domain under 90 days old
  🔴 DANGER — LIKELY SCAM            lookalike of a big brand, disguised letters,
                                     or under 30 days old + claim/wallet words

The alert shows one 🛡️ line per link. 🔴 adds a 🚨 warning at the top.
Nothing is ever blocked by this check — you decide. (Set BLOCK_DANGER = True
in config.py to drop 🔴 alerts instead.)

Results are cached in feedback.json for 30 days so the same domain isn't
looked up twice.
"""
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (MoneyRadar link check)"}
CACHE_DAYS = 30
MAX_LINKS = 3

# Shorteners and redirect hosts — resolved to their final destination
SHORTENERS = {
    "t.co", "bit.ly", "tinyurl.com", "ow.ly", "buff.ly", "is.gd", "cutt.ly",
    "rebrand.ly", "shorturl.at", "s.id", "lnkd.in", "linktr.ee", "rb.gy",
    "onelink.me", "app.link", "page.link", "adj.st", "go.link",
}

# Not opportunities themselves — no check needed
NEUTRAL = {
    "x.com", "twitter.com", "t.me", "telegram.me", "youtube.com", "youtu.be",
    "instagram.com", "tiktok.com", "facebook.com", "discord.gg", "discord.com",
    "medium.com", "github.com", "apps.apple.com", "play.google.com",
}

# Official domains of brands people get phished with. Anything that LOOKS like
# one of these but isn't on the list is a lookalike.
OFFICIAL = {
    "binance": {"binance.com", "binance.info", "binance.us"},
    "bybit": {"bybit.com", "bybitglobal.com"},
    "okx": {"okx.com"},
    "bitget": {"bitget.com", "bitgetapp.com", "bitget.site"},
    "kucoin": {"kucoin.com"},
    "mexc": {"mexc.com", "mexc.co"},
    "gateio": {"gate.io", "gate.com"},
    "coinbase": {"coinbase.com"},
    "kraken": {"kraken.com"},
    "metamask": {"metamask.io"},
    "phantom": {"phantom.app", "phantom.com"},
    "trustwallet": {"trustwallet.com"},
    "uniswap": {"uniswap.org"},
    "opensea": {"opensea.io"},
    "galxe": {"galxe.com"},
    "zealy": {"zealy.io"},
    "opay": {"opayweb.com", "opay.ng"},
    "palmpay": {"palmpay.com"},
    "moniepoint": {"moniepoint.com"},
    "kuda": {"kuda.com"},
    "robinhood": {"robinhood.com"},
    "phygitals": {"phygitals.com"},
}
OFFICIAL_DOMAINS = {d for ds in OFFICIAL.values() for d in ds}

RISKY_WORDS = ("claim", "airdrop", "connect", "wallet", "reward", "bonus", "verify",
               "drop", "free", "giveaway", "unlock", "migrate", "restore", "sync")

# two-part public suffixes, so "app.com.ng" -> "app.com.ng" not "com.ng"
TWO_PART_SUFFIXES = {
    "com.ng", "org.ng", "gov.ng", "edu.ng", "net.ng", "co.uk", "org.uk",
    "com.au", "co.za", "com.br", "co.in", "com.gh", "co.ke", "com.sg", "co.jp",
}

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+|(?<![@\w])(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s<>\"')\]]*", re.I)


def _root(host):
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _edit_distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _lookalike(root, host):
    """Return the brand this domain imitates, or None."""
    if root in OFFICIAL_DOMAINS:
        return None
    if "xn--" in host:
        return "a brand (punycode / disguised letters)"
    label = root.split(".")[0]
    # undo common letter swaps: 0→o, 1/l→i, 3→e  (b1nance, blnance → binance)
    fix = lambda s: s.replace("0", "o").replace("1", "i").replace("l", "i").replace("3", "e")
    squashed = fix(label.replace("-", "").replace("_", ""))
    tokens = {fix(t) for t in re.split(r"[-_]", label) if t}
    for brand_raw in OFFICIAL:
        brand = fix(brand_raw)
        if len(brand) <= 4:
            # short names (okx, kuda, opay, mexc) only as a whole word:
            # mexc-airdrop.com yes, zenopay.com no
            if brand in tokens or squashed == brand:
                return brand_raw
            continue
        if brand in squashed:
            return brand_raw      # e.g. bybit-rewards.xyz, binanceclaim.com, b1nance.com
        # one-letter typos only for longer names (short ones match real words too)
        if len(brand) >= 7 and _edit_distance(squashed, brand) <= 1:
            return brand_raw      # e.g. coinbaze.com, metamsak.io
    return None


def _resolve(url):
    """Follow redirects (short links) to the final URL. Never downloads the page body."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True, stream=True)
        final = r.url
        r.close()
        return final
    except requests.RequestException:
        return url


def _domain_age_days(root, cache):
    """Days since the domain was registered (via RDAP), or None if unknown."""
    hit = cache.get(root)
    today = date.today()
    if hit and (today - date.fromisoformat(hit["checked"])).days < CACHE_DAYS:
        created = hit.get("created")
    else:
        created = None
        try:
            r = requests.get(f"https://rdap.org/domain/{root}", headers=HEADERS, timeout=10)
            if r.status_code == 200:
                for ev in r.json().get("events", []) or []:
                    if ev.get("eventAction") == "registration" and ev.get("eventDate"):
                        created = ev["eventDate"][:10]
                        break
        except (requests.RequestException, ValueError):
            pass
        cache[root] = {"created": created, "checked": today.isoformat()}
    if not created:
        return None
    try:
        return (today - datetime.strptime(created, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def _age_text(days):
    if days is None:
        return "age unknown"
    if days < 60:
        return f"registered {days} day{'s' if days != 1 else ''} ago"
    if days < 730:
        return f"registered {days // 30} months ago"
    return f"registered {days // 365} years ago"


def check_url(url, cache):
    """Return (level, text) for one link. level: ok / unknown / caution / danger / skip."""
    if not url.lower().startswith("http"):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    root = _root(host)

    if root in SHORTENERS or host in SHORTENERS:
        final = _resolve(url)
        fhost = (urlparse(final).hostname or "").lower()
        if fhost and _root(fhost) != root:
            level, text = check_url(final, cache)
            return level, f"{root} → {text}"
    if root in NEUTRAL or host in NEUTRAL:
        return "skip", host if host in NEUTRAL else root

    full = (host + urlparse(url).path).lower()
    brand = _lookalike(root, host)
    if brand:
        return "danger", f"{root} — looks like {brand} but is NOT its official site"

    if root in OFFICIAL_DOMAINS:
        return "ok", f"{root} (official)"

    days = _domain_age_days(root, cache)
    risky = any(w in full for w in RISKY_WORDS)
    if days is not None and days < 30 and risky:
        return "danger", f"{root} — {_age_text(days)}, uses claim/wallet words"
    if days is not None and days < 90:
        return "caution", f"{root} — {_age_text(days)}, very new"
    if days is None:
        return "unknown", f"{root} — {_age_text(days)}"
    return "ok", f"{root} — {_age_text(days)}"


# Plain-words labels so you never have to remember what a colour means
LABEL = {
    "official": "✅ OFFICIAL SITE",
    "ok": "✅ LOOKS LEGIT (established site)",
    "unknown": "⚪ CAN'T VERIFY (age hidden) — be careful",
    "caution": "🟡 CAUTION — VERY NEW SITE",
    "danger": "🔴 DANGER — LIKELY SCAM",
}
RANK = {"skip": 0, "ok": 1, "unknown": 2, "caution": 3, "danger": 4}


def check_post(post, cache):
    """
    Check the links in a post. Returns {"level": worst level, "lines": [...]}.
    Uses expanded links from the tweet when available, else the text.
    """
    urls = list(post.get("links") or [])
    for m in URL_RE.findall(post.get("body") or ""):
        if m not in urls:
            urls.append(m)

    seen, lines, worst = set(), [], "skip"
    for url in urls:
        if len(lines) >= MAX_LINKS:
            break
        try:
            level, text = check_url(url.rstrip(".,!?:;…"), cache)
        except Exception as e:   # a link check must never stop an alert
            print(f"[link_check] {url}: {e}")
            continue
        dest = text.split(" → ")[-1]          # same final site via t.co and expanded link = one line
        if level == "skip" or dest in seen:
            continue
        seen.add(dest)
        label = LABEL["official"] if text.endswith("(official)") else LABEL[level]
        lines.append(f"{label}\n   {text.replace(' (official)', '')}")
        if RANK[level] > RANK[worst]:
            worst = level
    return {"level": worst, "lines": lines}
