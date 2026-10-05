"""
MoneyRadar Agent — Filtering logic
Cheap keyword pre-filter. Decides whether a post is worth sending to the AI
check, and makes a first guess at its tier:
  cash    — stated money/token reward >= threshold
  airdrop — task-based airdrop (no stated amount needed)
  points  — points/XP with conversion language (AI confirms it's instant)
"""
import re
from config import (
    OPPORTUNITY_KEYWORDS, CATEGORY_KEYWORDS, EXCLUDE_KEYWORDS,
    AIRDROP_KEYWORDS, POINTS_KEYWORDS, CONVERSION_KEYWORDS,
    MIN_NAIRA, MIN_USD, MIN_KRW,
)

# Number with optional size suffix: 150, 1,500, 2.5, 150K, 4M, 1.2B, 5万, 3千
_NUM = r'(\d[\d,]*(?:\.\d+)?(?:\s*(?:k|m|b|mn|bn|million|billion|thousand)(?![a-z])|万|千)?)'

_SUFFIX = {
    "k": 1e3, "thousand": 1e3, "千": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "万": 1e4,
}
_SPLIT_NUM = re.compile(r'^([\d,]*\.?\d+)\s*(.*)$')


def _to_number(raw):
    """'150K' -> 150000.0, '1,500' -> 1500.0, '5万' -> 50000.0"""
    m = _SPLIT_NUM.match(raw.strip().lower())
    if not m:
        raise ValueError(raw)
    value = float(m.group(1).replace(",", ""))
    return value * _SUFFIX.get(m.group(2), 1)

# Matches: ₦500, N500, 500 naira, ngn500, ₦ 1,000, N1000
NAIRA_PATTERN = re.compile(
    r'(?:₦\s*|\bngn\s*|\bnaira\s*|\bn(?=\d))' + _NUM + r'|' + _NUM + r'\s*(?:naira|ngn)\b',
    re.IGNORECASE,
)

# Matches: $5, 18$, USD5, 5 usd, 5 dollars, 10 USDT, 10U, 10 USDC, 5美元, 100刀
USD_PATTERN = re.compile(
    r'(?:\$|\busd[tc]?|美元|美金)\s*' + _NUM +
    r'|' + _NUM + r'\s*(?:usd[tc]?\b|dollars?\b|美元|美金|刀|u(?![a-z])|\$)',
    re.IGNORECASE,
)

# Matches: ₩1500, 1,500원, 5만원 (= 50,000)
KRW_PATTERN = re.compile(r'₩\s*' + _NUM + r'|' + _NUM + r'\s*원')
KRW_MAN_PATTERN = re.compile(r'(\d+(?:\.\d+)?)\s*만\s*원')


def _extract_amount(pattern, text):
    """Return the largest matched number from a regex pattern, or None."""
    amounts = []
    for match in pattern.finditer(text):
        for group in match.groups():
            if group:
                try:
                    amounts.append(_to_number(group))
                except ValueError:
                    continue
    return max(amounts) if amounts else None


def _extract_krw(text):
    amounts = []
    base = _extract_amount(KRW_PATTERN, text)
    if base is not None:
        amounts.append(base)
    for match in KRW_MAN_PATTERN.finditer(text):
        try:
            amounts.append(float(match.group(1)) * 10000)
        except ValueError:
            continue
    return max(amounts) if amounts else None


def _compile(keywords):
    """
    Latin keywords match as whole words (optional plural s), so "bet" doesn't
    hit "better"/"beta" and "referral" still hits "referrals".
    Chinese/Korean keywords match as plain substrings (no word spaces).
    """
    compiled = []
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower.isascii():
            compiled.append(re.compile(
                r'(?<![a-z0-9])' + re.escape(kw_lower) + r's?(?![a-z0-9])'
            ))
        else:
            compiled.append(kw_lower)
    return compiled


def _matches_any(text_lower, compiled):
    for item in compiled:
        if isinstance(item, str):
            if item in text_lower:
                return True
        elif item.search(text_lower):
            return True
    return False


_OPPORTUNITY = _compile(OPPORTUNITY_KEYWORDS)
_CATEGORY = _compile(CATEGORY_KEYWORDS)
_EXCLUDE = _compile(EXCLUDE_KEYWORDS)
_AIRDROP = _compile(AIRDROP_KEYWORDS)
_POINTS = _compile(POINTS_KEYWORDS)
_CONVERSION = _compile(CONVERSION_KEYWORDS)


def has_opportunity_keyword(text):
    return _matches_any(text.lower(), _OPPORTUNITY)


def has_category_keyword(text):
    return _matches_any(text.lower(), _CATEGORY)


def has_excluded_keyword(text):
    """Betting/gambling, gift cards, noise formats, scam tells — reject regardless."""
    return _matches_any(text.lower(), _EXCLUDE)


def has_airdrop_keyword(text):
    return _matches_any(text.lower(), _AIRDROP)


def has_points_conversion(text):
    text_lower = text.lower()
    return _matches_any(text_lower, _POINTS) and _matches_any(text_lower, _CONVERSION)


def meets_currency_threshold(text):
    """
    Returns (passes: bool, matched_amount: str|None) — passes if the post
    mentions at least MIN_NAIRA naira, MIN_USD dollars/stablecoins, or MIN_KRW won.
    """
    naira_amount = _extract_amount(NAIRA_PATTERN, text)
    usd_amount = _extract_amount(USD_PATTERN, text)
    krw_amount = _extract_krw(text)

    if naira_amount is not None and naira_amount >= MIN_NAIRA:
        return True, f"₦{naira_amount:,.0f}"
    if usd_amount is not None and usd_amount >= MIN_USD:
        return True, f"${usd_amount:,.2f}"
    if krw_amount is not None and krw_amount >= MIN_KRW:
        return True, f"₩{krw_amount:,.0f}"

    return False, None


def evaluate_post(title, body):
    """
    Main filter entry point. Returns a dict:
    { passes: bool, reason: str, matched_amount: str|None, tier_hint: str|None }
    tier_hint is "cash", "airdrop" or "points" — the AI step makes the final call.
    """
    full_text = f"{title} {body}"

    if has_excluded_keyword(full_text):
        return {"passes": False, "reason": "matched an excluded keyword (betting/gambling, gift card, scam tell, or noise format)", "matched_amount": None, "tier_hint": None}

    is_opportunity = has_opportunity_keyword(full_text)
    is_airdrop = has_airdrop_keyword(full_text)
    is_points = has_points_conversion(full_text)

    if not (is_opportunity or is_airdrop or is_points):
        return {"passes": False, "reason": "no opportunity, airdrop or points-conversion keyword found", "matched_amount": None, "tier_hint": None}

    if not is_airdrop and not has_category_keyword(full_text):
        return {"passes": False, "reason": "no fintech/crypto/wallet category match", "matched_amount": None, "tier_hint": None}

    passes_amount, matched_amount = meets_currency_threshold(full_text)

    if passes_amount:
        if is_airdrop and not is_opportunity:
            tier_hint = "airdrop"
        elif is_points and not is_opportunity:
            tier_hint = "points"
        else:
            tier_hint = "cash"
        return {"passes": True, "reason": "matched keyword + category + amount threshold", "matched_amount": matched_amount, "tier_hint": tier_hint}

    if is_airdrop:
        return {"passes": True, "reason": "matched task-airdrop keyword (no amount needed)", "matched_amount": None, "tier_hint": "airdrop"}

    if is_points:
        return {"passes": True, "reason": "matched points + conversion keywords", "matched_amount": None, "tier_hint": "points"}

    return {"passes": False, "reason": "no amount ≥ ₦500 / $1 / ₩1500 and not an airdrop or points campaign", "matched_amount": None, "tier_hint": None}
