"""
MoneyRadar Agent — Filtering logic
Decides whether a post is a genuine referral-bonus opportunity worth alerting on.
"""
import re
from config import REFERRAL_KEYWORDS, CATEGORY_KEYWORDS, MIN_NAIRA, MIN_USD

NAIRA_PATTERN = re.compile(
    r'(?:₦|ngn|naira)\s*([\d,]+)|(\d[\d,]*)\s*(?:naira|ngn)',
    re.IGNORECASE
)

USD_PATTERN = re.compile(
    r'(?:\$|usd)\s*([\d,]+(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:usd|dollars?)',
    re.IGNORECASE
)


def _extract_amount(pattern, text):
    amounts = []
    for match in pattern.finditer(text):
        for group in match.groups():
            if group:
                try:
                    amounts.append(float(group.replace(",", "")))
                except ValueError:
                    continue
    return max(amounts) if amounts else None


def has_referral_keyword(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in REFERRAL_KEYWORDS)


def has_category_keyword(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in CATEGORY_KEYWORDS)


def meets_currency_threshold(text):
    naira_amount = _extract_amount(NAIRA_PATTERN, text)
    usd_amount = _extract_amount(USD_PATTERN, text)

    if naira_amount is not None and naira_amount >= MIN_NAIRA:
        return True, f"₦{naira_amount:,.0f}"
    if usd_amount is not None and usd_amount >= MIN_USD:
        return True, f"${usd_amount:,.2f}"

    return False, None


def evaluate_post(title, body):
    full_text = f"{title} {body}"

    if not has_referral_keyword(full_text):
        return {"passes": False, "reason": "no referral keyword found"}

    if not has_category_keyword(full_text):
        return {"passes": False, "reason": "no fintech/crypto/wallet category match"}

    passes_amount, matched_amount = meets_currency_threshold(full_text)
    if not passes_amount:
        return {"passes": False, "reason": "no amount ≥ ₦500 / $1 mentioned"}

    return {
        "passes": True,
        "reason": "matched referral + category + amount threshold",
        "matched_amount": matched_amount,
    }
