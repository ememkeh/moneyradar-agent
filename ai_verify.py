"""
MoneyRadar Agent — AI verification step
Uses Claude Haiku (cheap + fast) to catch what keyword matching can't: whether
a post that already passed the keyword filter is a real, currently-live earning
opportunity, which alert tier it belongs to, and a one-line English summary
(so Chinese/Korean posts arrive readable).

Only runs on posts that already passed the cheap keyword filter, so cost stays
low — a few hundred tokens per check, a fraction of a cent each.

Setup: set the ANTHROPIC_API_KEY environment variable (GitHub Actions secret).
If unset, or if the API call fails:
  - cash-tier posts pass through on keyword match alone (fails open, as before)
  - airdrop/points posts are dropped (fails closed — they have no amount
    guardrail, so a broken key would otherwise flood you)
"""
import os
import re
import json
import requests
from config import TIER_LABELS, POINTS_TIER_ENABLED
import config

ALLOW_FREE_CHANCE_CREDIT = getattr(config, "ALLOW_FREE_CHANCE_CREDIT", False)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

VALID_TIERS = ("cash", "airdrop", "points")

SYSTEM_PROMPT = """You classify social media / Telegram posts for an alert bot that finds \
earning opportunities for crypto users, mainly in Nigeria. Posts may be in English, \
Chinese, Korean or other languages. Decide whether the post describes a genuine, \
currently-live opportunity, and which tier it belongs to.

TIERS:
- "cash": a specific stated reward in money or a named token amount — referral, \
signup/welcome, deposit or trading bonuses, cashback, naira offers from ANY app \
(new or little-known apps launching a paying referral program are exactly what \
we want — never reject an app just because it's unknown), KOL posts sharing an \
app's signup/referral reward with steps ("free $18 + $1 per ref"), survey rewards, task/quest \
rewards with a fixed payout, exchange campaigns with a fixed per-user reward. \
"cash" means every eligible user gets a stated amount. A prize pool, leaderboard, \
raffle, lucky draw or "N random winners" is NOT cash — the pool size is not the \
user's reward. Classify those as "airdrop" if they have concrete tasks, else reject.
- "airdrop": a live task-based airdrop or campaign where doable steps (connect X, \
follow, join Telegram/Discord, create an account, deposit or trade a set amount, \
Galxe/Zealy/Layer3/TaskOn quests, testnet interactions) make the user eligible for \
a token distribution, even if the exact amount isn't stated. It must name the \
project and the concrete steps, and be live now or have a stated claim/distribution \
date. An airdrop claim or eligibility check that is open now also counts.
- "points": points/XP/credits ONLY when they can be converted to money or tokens \
right now at a stated rate or are redeemable/withdrawable immediately \
(e.g. "1,000 pts = 1 USDT, redeem anytime").

REJECT (verified false, tier "none") if the post:
- Is vague hype with no named project, reward or steps ("don't miss it", "good project")
- Is speculative points/XP farming with no instant conversion ("airdrop potential: high", \
farm points for a possible future TGE, multipliers/streaks/roles with no payout)
- Only mentions a keyword in passing: news, price talk, analysis, or a recap of an \
airdrop that already distributed
- Is about betting, gambling, or gift cards{chance_rule}
- Describes an offer that has clearly ended or expired
- Looks like a scam: asks for a seed phrase or private key, "send crypto to receive \
more", claim links on lookalike/unofficial domains asking for wallet signatures, \
"drop your wallet address" engagement bait, guaranteed returns, impersonating an exchange
- Sells something: paid groups, VIP signals, courses, paid promotion slots
- Is a pay-to-earn scheme: you must pay a fee, buy a package/level, or "invest" \
to unlock earnings, daily returns on a deposit ("deposit ₦5,000, earn ₦500 daily"), \
or rewards that only come from recruiting people who pay. NOTE: a deposit or \
purchase the user KEEPS is fine — e.g. an exchange's "deposit $20, buy $15 of \
crypto, get $5 bonus", or "buy a coffee and post a photo, we send 5,000 sats". \
Those are normal "cash" offers. Only reject when money is paid AWAY or locked \
for promised daily returns.
- Is a personal farming update or KOL commentary rather than a campaign: "just added \
this to my farm list", "been farming X", "here's what I did", threads reviewing a \
project — unless it also gives the official link and the exact steps to qualify

project: the name of the project/app/exchange the offer is FOR (not the poster), \
lowercase, no @ or spaces, e.g. "conso", "phygitals", "bybit". Use the official \
name even if the post only tags its @handle (@conso_xyz -> "conso"). Empty string \
if rejected.

Learn-and-earn offers (watch lessons, get $X of a token) and brand giveaways \
("we'll send you 5,000 sats", "first come, first served") are "cash".

summary_en: one English line, max 25 words — what it is, the reward, the main steps. \
If the offer is limited to certain countries (stated, or a well-known US/EU-only \
app like Robinhood), start with the region in brackets, e.g. "[US/EU only]" or \
"[Nigeria & Ghana]". Never reject just because of region. \
Translate if the post isn't in English. Empty string if rejected.

Respond with ONLY a JSON object, nothing else:
{"verified": true or false, "tier": "cash" | "airdrop" | "points" | "none", \
"reason": "one short sentence", "project": "name", "summary_en": "one line"}"""

_CHANCE_ALLOWED = """ — EXCEPTION: free in-app credit (from signup or \
referrals, no deposit needed) that the user can turn into withdrawable cash counts \
as "cash" — e.g. Phygitals: $1 per referral used to open a card pack, card sold \
instantly for cash. summary_en should say how the credit becomes cash. Still \
reject if the user must deposit their own money to take part."""

SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{chance_rule}", _CHANCE_ALLOWED if ALLOW_FREE_CHANCE_CREDIT else ""
)

_RESPOND_SINGLE = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Respond with ONLY a JSON object"):]
_PROMPT_CORE = SYSTEM_PROMPT[:SYSTEM_PROMPT.index("Respond with ONLY a JSON object")]

_RESPOND_BATCH = """You will receive several posts, each starting with "### POST <n>". \
Judge each one on its own. Respond with ONLY a JSON array, one object per post, \
in the same order, nothing else:
[{"n": 1, "verified": true or false, "tier": "cash" | "airdrop" | "points" | "none", \
"reason": "one short sentence", "project": "name", "summary_en": "one line"}, ...]"""


# ── OWNER FEEDBACK (from 👍 / 👎 taps on alerts, set by feedback.py) ──
FEEDBACK = {"text": ""}


def set_feedback_examples(likes, dislikes):
    """Show the AI what the owner liked / didn't like, as extra guidance."""
    parts = []
    if likes:
        parts.append("The bot owner LIKED alerts like these — accept similar posts:\n"
                     + "\n".join(f"- {s}" for s in likes))
    if dislikes:
        parts.append("The bot owner DISLIKED alerts like these — reject similar posts "
                     "(same kind of offer, same weakness):\n"
                     + "\n".join(f"- {s}" for s in dislikes))
    FEEDBACK["text"] = ("\n\n" + "\n\n".join(parts) + "\n\n") if parts else ""


def _system(batch=False):
    return _PROMPT_CORE + FEEDBACK["text"] + (_RESPOND_BATCH if batch else _RESPOND_SINGLE)


# ── SPEND TRACKING (Haiku 4.5: $1 / 1M input tokens, $5 / 1M output) ──
USAGE = {"calls": 0, "input": 0, "output": 0}
PRICE_IN, PRICE_OUT = 1.0, 5.0


def _track(data):
    u = (data or {}).get("usage") or {}
    USAGE["calls"] += 1
    USAGE["input"] += int(u.get("input_tokens", 0) or 0)
    USAGE["output"] += int(u.get("output_tokens", 0) or 0)


def usage_cost():
    return USAGE["input"] / 1e6 * PRICE_IN + USAGE["output"] / 1e6 * PRICE_OUT


def _strip_fences(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return raw


# Set when the Anthropic account can't be used (credits empty, bad key).
# While set, NOTHING is alerted — no AI means no filtering, and unfiltered
# alerts are noise. main.py sends you one Telegram warning a day instead.
AI_DOWN = {"reason": None}

_ACCOUNT_ERROR_HINTS = ("credit balance", "billing", "insufficient", "invalid x-api-key",
                        "authentication", "permission", "disabled")


def _check_account_error(resp):
    """Detect 'out of credits' / bad-key errors and switch the bot to safe mode."""
    if resp.status_code in (400, 401, 402, 403):
        body = resp.text.lower()
        if resp.status_code in (401, 402, 403) or any(h in body for h in _ACCOUNT_ERROR_HINTS):
            AI_DOWN["reason"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
            print(f"[ai_verify] ⚠️ Anthropic account problem — {AI_DOWN['reason']}")
            return True
    return False


def _fail_result(tier_hint, why):
    """What to return when the AI check can't run."""
    if AI_DOWN["reason"]:
        return {"verified": False, "tier": tier_hint, "reason": "AI unavailable (credits/key) — held back", "summary_en": "", "project": ""}
    if tier_hint in (None, "cash"):
        return {"verified": True, "tier": "cash", "reason": why, "summary_en": "", "project": ""}
    return {"verified": False, "tier": tier_hint, "reason": why, "summary_en": "", "project": ""}


def _clean_verdict(verdict, tier_hint):
    verified = bool(verdict.get("verified", False))
    tier = str(verdict.get("tier", "none")).lower()
    reason = verdict.get("reason", "")
    summary = verdict.get("summary_en", "") or ""
    project = re.sub(r"[^a-z0-9]", "", str(verdict.get("project", "")).lower())

    if verified and tier not in VALID_TIERS:
        tier = tier_hint if tier_hint in VALID_TIERS else "cash"
    if verified and tier == "points" and not POINTS_TIER_ENABLED:
        verified = False
        reason = "points tier disabled in config"

    return {"verified": verified, "tier": tier, "reason": reason, "summary_en": summary, "project": project}


BATCH_SIZE = 10


def classify_many(items):
    """
    Check many posts with as few Claude calls as possible (BATCH_SIZE per call).
    The long instructions are sent once per batch instead of once per post,
    which is most of the cost. items: list of (title, body, tier_hint).
    Returns one verdict dict per item, same order. If a batch reply can't be
    read, that batch falls back to one-by-one checks so nothing is lost.
    """
    results = []
    for start in range(0, len(items), BATCH_SIZE):
        chunk = items[start:start + BATCH_SIZE]
        results.extend(_classify_chunk(chunk))
    return results


def _classify_chunk(chunk):
    if len(chunk) == 1 or AI_DOWN["reason"]:
        return [classify_opportunity(t, b, h) for t, b, h in chunk]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return [classify_opportunity(t, b, h) for t, b, h in chunk]

    blocks = []
    for n, (title, body, hint) in enumerate(chunk, 1):
        text = f"{title}\n\n{body}"[:1500]
        guess = f"[keyword filter guess: {hint}]\n" if hint else ""
        blocks.append(f"### POST {n}\n{guess}{text}")
    content = "\n\n".join(blocks)

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                # generous cap — you only pay for tokens actually written, and a
                # cut-off reply would force 10 separate (pricier) calls
                "max_tokens": 250 * len(chunk) + 300,
                "system": _system(batch=True),
                "messages": [{"role": "user", "content": content}],
            },
            timeout=60,
        )
        if _check_account_error(resp):
            return [_fail_result(h, "AI unavailable") for _, _, h in chunk]
        resp.raise_for_status()
        data = resp.json()
        _track(data)
        if data.get("stop_reason") == "max_tokens":
            print("[ai_verify] batch reply was cut off (max_tokens)")
        parsed = json.loads(_strip_fences(data["content"][0]["text"]))
        by_n = {int(v.get("n", 0)): v for v in parsed if isinstance(v, dict)}
        out = []
        for n, (t, b, h) in enumerate(chunk, 1):
            if n in by_n:
                out.append(_clean_verdict(by_n[n], h))
            else:
                out.append(classify_opportunity(t, b, h))   # missing from reply — check alone
        return out
    except Exception as e:
        print(f"[ai_verify] batch check failed ({e}) — checking one by one")
        return [classify_opportunity(t, b, h) for t, b, h in chunk]


def classify_opportunity(title, body, tier_hint=None):
    """
    Returns a dict: { verified: bool, tier: str, reason: str, summary_en: str, project: str }
    tier is "cash", "airdrop" or "points" (or "none" when rejected).
    tier_hint is the keyword filter's guess from evaluate_post().
    """
    if AI_DOWN["reason"]:
        return _fail_result(tier_hint, "AI unavailable")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        AI_DOWN["reason"] = "ANTHROPIC_API_KEY secret is missing"
        return _fail_result(tier_hint, "AI check skipped (no ANTHROPIC_API_KEY set)")

    text = f"{title}\n\n{body}"[:2000]  # cap length — keeps cost predictable
    if tier_hint:
        text = f"[keyword filter guess: {tier_hint}]\n\n{text}"

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 300,
                "system": _system(),
                "messages": [{"role": "user", "content": text}],
            },
            timeout=20,
        )
        if _check_account_error(resp):
            return _fail_result(tier_hint, "AI unavailable")
        resp.raise_for_status()
        data = resp.json()
        _track(data)
        raw = data["content"][0]["text"].strip()

        # Strip markdown fences if the model wraps its JSON in them
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        return _clean_verdict(json.loads(raw), tier_hint)

    except Exception as e:
        print(f"[ai_verify] AI check failed: {e}")
        return _fail_result(tier_hint, "AI check failed — passed through" if tier_hint in (None, "cash") else "AI check failed — dropped")


SAME_CAMPAIGN_PROMPT = """You compare a new earning-opportunity post against alerts \
already sent for the same project. Decide if the new post is the SAME campaign \
(same reward and same tasks/steps — just reposted, reworded, or shared by a \
different person, or a minor reminder/update about it) or a NEW campaign \
(different reward, different tasks, a new season/round/phase, a new claim \
opening, a new product or new referral program).

Respond with ONLY a JSON object:
{"same": true or false, "reason": "one short sentence"}"""


def is_same_campaign(project, new_summary, new_text, previous_summaries):
    """
    True if the new post is a repeat of a campaign already alerted for this
    project. If the AI check can't run, compares summaries exactly (so a
    broken key doesn't block genuinely new campaigns, and exact repeats
    still get caught).
    """
    if not previous_summaries:
        return False

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return new_summary.strip().lower() in [s.strip().lower() for s in previous_summaries]

    prev = "\n".join(f"- {s}" for s in previous_summaries[-10:])
    content = (
        f"Project: {project}\n\n"
        f"Alerts already sent:\n{prev}\n\n"
        f"New post summary: {new_summary}\n\n"
        f"New post text:\n{new_text[:1500]}"
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 120,
                "system": SAME_CAMPAIGN_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=20,
        )
        if _check_account_error(resp):
            return True   # can't compare — treat as repeat, nothing alerts while AI is down
        resp.raise_for_status()
        data = resp.json()
        _track(data)
        verdict = json.loads(_strip_fences(data["content"][0]["text"]))
        same = bool(verdict.get("same", False))
        print(f"[ai_verify] campaign check '{project}': {'SAME' if same else 'NEW'} — {verdict.get('reason', '')}")
        return same
    except Exception as e:
        print(f"[ai_verify] campaign check failed: {e}")
        return new_summary.strip().lower() in [s.strip().lower() for s in previous_summaries]


def verify_opportunity(title, body, tier_hint=None):
    """
    Backward-compatible wrapper for the current main.py.
    Returns (verified: bool, reason: str). The reason now starts with the tier
    label and carries the English summary, e.g.
    "🪂 task airdrop — Connect X + wallet on Galxe to qualify for XYZ drop"
    """
    result = classify_opportunity(title, body, tier_hint)
    label = TIER_LABELS.get(result["tier"], "")
    detail = result["summary_en"] or result["reason"]
    reason = f"{label} — {detail}" if label else detail
    return result["verified"], reason
