"""
MoneyRadar Agent — AI verification step
Uses Claude Haiku (cheap + fast) to catch what keyword matching can't: whether a
post that already passed the keyword filter is actually describing a specific,
currently-live earning opportunity — not vague hype, an airdrop-farming pitch
dressed in referral language, or a keyword mentioned in passing with no real offer.

Only runs on posts that already passed the cheap keyword filter, so cost stays
low — a few hundred tokens per check, a fraction of a cent each.

Setup: set the ANTHROPIC_API_KEY environment variable (GitHub Actions secret).
If unset, or if the API call fails for any reason, this step is skipped and the
post passes through on keyword-match alone (fails open — a missing/broken key
never silently blocks every alert, it just means you're back to keyword-only
filtering until it's fixed).
"""
import os
import json
import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You judge whether a social media post describes a genuine, specific, \
currently-active earning opportunity in fintech, crypto, wallets, cross-border \
payments, or web3. Valid opportunity types include referral bonuses, cashback, \
registration/welcome bonuses, survey rewards, and task/quest rewards (including ones \
that pay in a crypto token) — not just refer-a-friend links.

ACCEPT if the post clearly states:
- A specific reward (a dollar figure, a naira figure, or a named token amount)
- A concrete, doable set of steps to get it
- That it's live/currently claimable now (not a future/planned reward)

REJECT if the post:
- Is vague hype with no concrete offer or reward amount ("don't miss it", "good project")
- Is a speculative points-farming pitch with no stated near-term value — heavy task \
lists, "airdrop potential: high", vague future token rewards with no dollar or token \
amount given, multipliers/roles/streaks with no concrete payout stated
- Only mentions a keyword in passing without describing an actual current offer
- Is about betting, gambling, or gift cards
- Describes an offer that has clearly already ended or expired

The key distinction for task/quest/survey-reward posts: accept when the reward is \
concrete and stated (e.g. "get 70 $GEOD (~$16) for connecting your wallet and \
following our page" — specific token amount, specific dollar equivalent, specific \
steps). Reject when it's an open-ended farming pitch with no stated payout (e.g. a \
long list of tasks and multipliers with only vague "high potential" language and no \
number attached).

Respond with ONLY a JSON object, nothing else: {"verified": true or false, "reason": "one short sentence"}"""


def verify_opportunity(title, body):
    """
    Returns (verified: bool, reason: str).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return True, "AI check skipped (no ANTHROPIC_API_KEY set)"

    text = f"{title}\n\n{body}"[:2000]  # cap length — keeps cost predictable

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
                "max_tokens": 100,
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
        return bool(verdict.get("verified", False)), verdict.get("reason", "")
    except Exception as e:
        print(f"[ai_verify] AI check failed, passing through on keyword-match alone: {e}")
        return True, "AI check failed — passed through"
