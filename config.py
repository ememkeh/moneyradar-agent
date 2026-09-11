"""
MoneyRadar Agent — Configuration
Edit this file to tune what the agent looks for. No code changes needed elsewhere.
"""

# ── SUBREDDITS TO SCAN ──
SUBREDDITS = [
    "Nigeria",
    "NigerianDiaspora",
    "CryptoCurrency",
    "defi",
    "fintech",
    "passive_income",
    "beermoney",
    "referral",
]

# ── X (TWITTER) SEARCH TERMS ──
X_SEARCH_QUERIES = [
    "referral bonus naira",
    "refer and earn nigeria",
    "invite code crypto wallet",
    "referral bonus usdt",
]

# ── KEYWORDS THAT MUST APPEAR (post must match at least one) ──
REFERRAL_KEYWORDS = [
    "referral", "refer a friend", "refer and earn", "invite code",
    "invite friend", "refer friend", "referral bonus", "referral code",
    "refer & earn", "sign up bonus", "signup bonus",
]

# ── CATEGORY KEYWORDS (post must match at least one) ──
CATEGORY_KEYWORDS = [
    "fintech", "crypto", "wallet", "exchange", "bank", "banking",
    "payment", "remit", "remittance", "usdt", "usdc", "bitcoin",
    "card", "virtual card", "naira", "app", "cash app", "fund",
]

# ── MINIMUM PAYOUT THRESHOLDS ──
MIN_NAIRA = 500
MIN_USD = 1

# ── SCAN FREQUENCY ──
SCAN_FREQUENCY_HOURS = 6

# ── DEDUPE ──
SEEN_POSTS_FILE = "seen_posts.json"
MAX_SEEN_POSTS_STORED = 2000

# ── X (TWITTER) TOGGLE ──
X_ENABLED = False
