"""
MoneyRadar Agent — Nigeria eligibility check

Runs only on posts that are about to be ALERTED. Works cheapest-first, and
stops as soon as it has an answer:

  1. FREE  — the post itself mentions Nigeria / naira / 🇳🇬            -> YES
  2. FREE  — remembered answer for this site from the last 14 days
  3. FREE  — App Store link: is the app listed in the Nigerian store?  -> app listed / not listed
  4. FREE  — read the site's terms / FAQ / restricted-countries pages and
             pull out only the sentences about countries & eligibility
  5. PAID  — ONE small AI call reads just those sentences (~$0.001),
             and only when step 4 actually found something to read

If nothing can be read (app-only offer, page blocked, no link) the answer is
UNCLEAR and the alert still goes out. Nothing is hidden unless you turn on
HIDE_NOT_ELIGIBLE in config.py.
"""
import re
from datetime import date
from urllib.parse import urlparse

import requests

import ai_verify
import link_check

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
CACHE_DAYS = 14
MAX_PAGES = 8            # pages fetched per site, at most (stops early once Nigeria is found)
MAX_BYTES = 400_000      # per page
SNIPPET_BUDGET = 3500    # characters of terms text sent to the AI

# where sites usually keep eligibility / country rules
# most likely to name countries first
TERMS_PATHS = [
    "/legal/restricted-countries", "/restricted-countries", "/restricted-jurisdictions",
    "/supported-countries", "/terms", "/terms-of-service", "/eligibility", "/faq",
    "", "/legal/terms", "/terms-of-use", "/legal", "/help", "/countries",
]

NIGERIA_IN_POST = re.compile(r"\b(nigeria|nigerians?|naija|naira|ngn|lagos|abuja)\b|🇳🇬|₦", re.I)
KEYWORDS = re.compile(
    r"nigeria|restricted|prohibited|excluded|sanction|jurisdiction|eligib|"
    r"countr(y|ies)|residents?|available in|not available|supported regions?|kyc",
    re.I,
)
STORE_HOSTS = {"apps.apple.com", "itunes.apple.com"}
SKIP_ROOTS = {"x.com", "twitter.com", "t.me", "telegram.me", "youtube.com", "youtu.be",
              "instagram.com", "tiktok.com", "facebook.com", "discord.gg", "discord.com",
              "medium.com", "github.com", "google.com", "apple.com"}

LABEL = {
    "yes": "NIGERIA ELIGIBILITY: YES",
    "no": "NIGERIA ELIGIBILITY: NO",
    "unclear": "NIGERIA ELIGIBILITY: UNCLEAR",
}


def _text(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;|&#160;", " ", html)
    return re.sub(r"\s+", " ", html)


def _fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True, stream=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            r.close()
            return ""
        raw = r.raw.read(MAX_BYTES, decode_content=True) if hasattr(r, "raw") and r.raw else r.content
        r.close()
        return _text(raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw))
    except Exception:
        return ""


def _snippets(text):
    """Only the sentences about countries / eligibility — keeps the AI call tiny."""
    out = []
    for m in KEYWORDS.finditer(text):
        s, e = max(0, m.start() - 160), min(len(text), m.end() + 220)
        out.append(text[s:e].strip())
    # Nigeria mentions first, they matter most
    out.sort(key=lambda s: 0 if "nigeria" in s.lower() else 1)
    merged, total = [], 0
    for s in out:
        if any(s[:60] in x for x in merged):
            continue
        merged.append(s)
        total += len(s)
        if total > SNIPPET_BUDGET:
            break
    return merged


def _apple_listed_in_ng(url):
    m = re.search(r"/id(\d+)", url)
    if not m:
        return None
    try:
        r = requests.get(f"https://itunes.apple.com/lookup?id={m.group(1)}&country=ng",
                         headers=HEADERS, timeout=8)
        return r.json().get("resultCount", 0) > 0
    except Exception:
        return None


def check(post, cache):
    """
    Returns {"status": "yes"|"no"|"unclear", "line": "NIGERIA ELIGIBILITY: ... — ..."}.
    cache: dict kept in feedback.json (per-site answers for 14 days).
    """
    # 1. the post itself says it's for Nigeria
    body = f"{post.get('title', '')} {post.get('body', '')} {post.get('summary_en', '')}"
    if NIGERIA_IN_POST.search(body):
        return _result("yes", "the offer itself mentions Nigeria / naira")

    urls = (post.get("link_report") or {}).get("urls") or []
    if not urls:
        return _result("unclear", "no link to check — look at the post")

    store_note = None
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        root = link_check._root(host)

        # 3. App Store: is the app available in Nigeria?
        if host in STORE_HOSTS:
            listed = _apple_listed_in_ng(url)
            if listed is True:
                store_note = "the app is listed in the Nigerian App Store"
            elif listed is False:
                return _result("no", "the app is NOT in the Nigerian App Store")
            continue
        if root in SKIP_ROOTS or root in link_check.SHORTENERS:
            continue

        # 2. remembered answer for this site
        hit = cache.get(root)
        keep = CACHE_DAYS if hit and hit.get("status") != "unclear" else 2   # retry unclear sooner
        if hit and (date.today() - date.fromisoformat(hit["checked"])).days < keep:
            return _result(hit["status"], hit["evidence"] + " (checked recently)")

        # 4. read terms / FAQ pages, keep only the country sentences
        base = f"https://{host}"
        pages, texts = 0, []
        for path in [urlparse(url).path or ""] + TERMS_PATHS:
            if pages >= MAX_PAGES:
                break
            t = _fetch(base + path if path else url)
            pages += 1
            if t:
                texts.extend(_snippets(t))
            if sum(len(s) for s in texts) > SNIPPET_BUDGET or any("nigeria" in s.lower() for s in texts):
                break
        if not texts:
            status, evidence = ("yes", store_note) if store_note else (
                "unclear", f"couldn't read {root}'s terms — check manually")
            cache[root] = {"status": status, "evidence": evidence, "checked": date.today().isoformat()}
            return _result(status, evidence)

        # 5. one small AI call on just those sentences
        verdict = ai_verify.check_nigeria_eligibility(root, texts)
        status = verdict.get("status", "unclear")
        evidence = verdict.get("evidence") or ""
        if status == "unclear" and store_note:
            status, evidence = "yes", store_note
        evidence = (f"{evidence} ({root})" if evidence else f"nothing clear in {root}'s terms")
        cache[root] = {"status": status, "evidence": evidence, "checked": date.today().isoformat()}
        return _result(status, evidence)

    if store_note:
        return _result("yes", store_note)
    return _result("unclear", "no readable site linked — check manually")


def _result(status, evidence):
    return {"status": status, "line": f"{LABEL[status]} — {evidence}"}
