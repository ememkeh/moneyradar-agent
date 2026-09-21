"""
MoneyRadar Agent — Filtering logic
Decides whether a post is a genuine earning opportunity worth alerting on
(referral bonuses, cashback, registration/welcome bonuses — not just refer-a-friend).
"""
import re
from config import OPPORTUNITY_KEYWORDS, CATEGORY_KEYWORDS, EXCLUDE_KEYWORDS, MIN_NAIRA, MIN_USD

# Matches: ₦500, N500, 500 naira, ngn500, ₦ 1,000, N1000
NAIRA_PATTERN = re.compile(
    r'(?:₦|ngn|naira)\s*([\d,]+)|(\d[\d,]*)\s*(?:naira|ngn)',
    re.IGNORECASE
)

# Matches: $5, USD5, 5 usd, $ 5.00, 5 dollars
USD_PATTERN = re.compile(
    r'(?:\$|usd)\s*([\d,]+(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:usd|dollars?)',
    re.IGNORECASE
)


def _extract_amount(pattern, text):
    """Return the largest matched number from a regex pattern, or None."""
    amounts = []
    for match in pattern.finditer(text):
        for group in match.groups():
            if group:
                try:
                    amounts.append(float(group.replace(",", "")))
                except ValueError:
                    continue
    return max(amounts) if amounts else None


def has_opportunity_keyword(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in OPPORTUNITY_KEYWORDS)


def has_category_keyword(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in CATEGORY_KEYWORDS)


def has_excluded_keyword(text):
    """Betting/gambling, gift cards, and generic noise formats — reject regardless of anything else."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in EXCLUDE_KEYWORDS)


def meets_currency_threshold(text):
    """
    Returns (passes: bool, matched_amount: str) — passes if the post mentions
    at least MIN_NAIRA naira OR at least MIN_USD dollars.
    """
    naira_amount = _extract_amount(NAIRA_PATTERN, text)
    usd_amount = _extract_amount(USD_PATTERN, text)

    if naira_amount is not None and naira_amount >= MIN_NAIRA:
        return True, f"₦{naira_amount:,.0f}"
    if usd_amount is not None and usd_amount >= MIN_USD:
        return True, f"${usd_amount:,.2f}"

    return False, None


def evaluate_post(title, body):
    """
    Main filter entry point. Returns a dict:
    { passes: bool, reason: str, matched_amount: str|None }
    """
    full_text = f"{title} {body}"

    if has_excluded_keyword(full_text):
        return {"passes": False, "reason": "matched an excluded keyword (betting/gambling, gift card, or noise format)"}

    if not has_opportunity_keyword(full_text):
        return {"passes": False, "reason": "no opportunity keyword found (referral/cashback/bonus)"}

    if not has_category_keyword(full_text):
        return {"passes": False, "reason": "no fintech/crypto/wallet category match"}

    passes_amount, matched_amount = meets_currency_threshold(full_text)
    if not passes_amount:
        return {"passes": False, "reason": "no amount ≥ ₦500 / $1 mentioned"}

    return {
        "passes": True,
        "reason": "matched opportunity keyword + category + amount threshold",
        "matched_amount": matched_amount,
    }
