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
import json
import requests
from config import TIER_LABELS, POINTS_TIER_ENABLED

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

VALID_TIERS = ("cash", "airdrop", "points")

SYSTEM_PROMPT = """You classify social media / Telegram posts for an alert bot that finds \
earning opportunities for crypto users, mainly in Nigeria. Posts may be in English, \
Chinese, Korean or other languages. Decide whether the post describes a genuine, \
currently-live opportunity, and which tier it belongs to.

TIERS:
- "cash": a specific stated reward in money or a named token amount — referral, \
signup/welcome, deposit or trading bonuses, cashback, survey rewards, task/quest \
rewards with a fixed payout, exchange campaigns with a fixed per-user reward.
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
- Is about betting, gambling, or gift cards
- Describes an offer that has clearly ended or expired
- Looks like a scam: asks for a seed phrase or private key, "send crypto to receive \
more", claim links on lookalike/unofficial domains asking for wallet signatures, \
"drop your wallet address" engagement bait, guaranteed returns, impersonating an exchange
- Sells something: paid groups, VIP signals, courses, paid promotion slots

summary_en: one English line, max 25 words — what it is, the reward, the main steps. \
Translate if the post isn't in English. Empty string if rejected.

Respond with ONLY a JSON object, nothing else:
{"verified": true or false, "tier": "cash" | "airdrop" | "points" | "none", \
"reason": "one short sentence", "summary_en": "one line"}"""


def _fail_result(tier_hint, why):
    """What to return when the AI check can't run."""
    if tier_hint in (None, "cash"):
        return {"verified": True, "tier": "cash", "reason": why, "summary_en": ""}
    return {"verified": False, "tier": tier_hint, "reason": why, "summary_en": ""}


def classify_opportunity(title, body, tier_hint=None):
    """
    Returns a dict: { verified: bool, tier: str, reason: str, summary_en: str }
    tier is "cash", "airdrop" or "points" (or "none" when rejected).
    tier_hint is the keyword filter's guess from evaluate_post().
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
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
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["content"][0]["text"].strip()

        # Strip markdown fences if the model wraps its JSON in them
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        verdict = json.loads(raw)
        verified = bool(verdict.get("verified", False))
        tier = str(verdict.get("tier", "none")).lower()
        reason = verdict.get("reason", "")
        summary = verdict.get("summary_en", "")

        if verified and tier not in VALID_TIERS:
            tier = tier_hint if tier_hint in VALID_TIERS else "cash"
        if verified and tier == "points" and not POINTS_TIER_ENABLED:
            verified = False
            reason = "points tier disabled in config"

        return {"verified": verified, "tier": tier, "reason": reason, "summary_en": summary}

    except Exception as e:
        print(f"[ai_verify] AI check failed: {e}")
        return _fail_result(tier_hint, "AI check failed — passed through" if tier_hint in (None, "cash") else "AI check failed — dropped")


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
