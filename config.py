"""
MoneyRadar Agent — Configuration
Edit this file to tune what the agent looks for. No code changes needed elsewhere.
"""

# ── SUBREDDITS TO SCAN ──
# Add/remove freely. Agent scans r/<name>/new.json (public, no auth needed).
SUBREDDITS = [
    "Nigeria",
    "NigerianDiaspora",
    "CryptoCurrency",
    "defi",
    "fintech",
    "passive_income",
    "beermoney",          # US/global referral-bonus community — high signal for this niche
    "referral",
]

# ── X (TWITTER) SEARCH TERMS ──
# Only used if X_ENABLED=true and a valid TwitterAPI.io key is set (see README).
# Exclusions baked directly into each query (X search operators) as a first filter layer.
X_SEARCH_QUERIES = [
    "referral bonus fintech app -bet -betting -casino -gambling",
    "refer and earn crypto wallet -bet -betting -casino -gambling",
    "cross border payment referral -bet -betting -casino -gambling",
    "invite friends earn usdt -bet -betting -casino -gambling",
    "web3 wallet referral program -bet -betting -casino -gambling",
    "referral bonus remittance app -bet -betting -casino -gambling",
    "instant cashback crypto naira -bet -betting -casino -gambling",
    "registration bonus crypto exchange -bet -betting -casino -gambling",
    "welcome bonus crypto wallet -bet -betting -casino -gambling",
    "trading bonus naira crypto -bet -betting -casino -gambling",
    "complete survey earn usdc -bet -betting -casino -gambling",
    "claim reward crypto wallet -bet -betting -casino -gambling",
    "bonus code send money app -bet -betting -casino -gambling",
    "solana wallet task reward -bet -betting -casino -gambling",
    "sign up bonus polymarket -bet -betting -casino -gambling",
]

# ── KEYWORDS THAT MUST APPEAR (post must match at least one) ──
# Broadened beyond referral phrasing. Real posts observed in the wild use
# "survey reward", "claim reward", "bonus code", "quest reward" far more often
# than the word "referral" itself — matching only referral-style phrasing was
# missing most genuine cashback/task/survey/quest offers entirely.
OPPORTUNITY_KEYWORDS = [
    "referral", "refer a friend", "refer and earn", "invite code",
    "invite friend", "refer friend", "referral bonus", "referral code",
    "refer & earn", "sign up bonus", "signup bonus",
    "cashback", "instant cashback", "registration bonus", "welcome bonus",
    "deposit bonus", "trading bonus", "cash reward", "instant reward",
    "survey reward", "complete survey", "claim reward", "claim your",
    "bonus code", "task reward", "complete task", "quest reward",
    "sign up and get", "deposit and get",
]

# ── CATEGORY KEYWORDS (post must match at least one) ──
# Tightened to fintech / cross-border payments / crypto / wallets / web3 only.
# Removed overly broad terms ("app", "fund", "bank") that matched unrelated noise.
# Added "solana" and "polymarket" as specific, safe proper nouns (unlike bare
# "token", which would match almost anything crypto-adjacent).
CATEGORY_KEYWORDS = [
    "fintech", "crypto", "wallet", "exchange", "remit", "remittance",
    "cross-border", "cross border", "usdt", "usdc", "bitcoin", "stablecoin",
    "defi", "web3", "virtual card", "neobank", "naira", "cash app", "p2p",
    "solana", "polymarket", "international transfer", "money transfer", "send money",
]

# ── EXCLUDE KEYWORDS (post is rejected if ANY of these appear, no exceptions) ──
# Betting/gambling and gift cards per explicit request. Plus generic noise formats
# that aren't actual opportunity posts (curated "alpha" recap threads, giveaway
# lists).
# NOTE: bare "airdrop" was previously excluded, but that blocked genuine
# task/quest reward campaigns (e.g. TokenRun) that happen to use the word.
# The distinction between a real, specific, near-term-value campaign and a
# vague speculative points-farm is now handled by the AI verification step
# (ai_verify.py) instead of a single banned keyword.
EXCLUDE_KEYWORDS = [
    "bet", "betting", "casino", "gambl", "sportsbook", "wager", "odds", "parlay",
    "daily alpha", "giveaway list",
    "gift card", "giftcard", "gift-card",
]

# ── MINIMUM PAYOUT THRESHOLDS ──
MIN_NAIRA = 500
MIN_USD = 1

# ── SCAN FREQUENCY ──
# This value is informational only — actual frequency is controlled by the
# GitHub Actions cron schedule in .github/workflows/scan.yml (set to hourly).
SCAN_FREQUENCY_HOURS = 1

# ── DEDUPE ──
# Posts already alerted on are stored here so you don't get repeat pings.
SEEN_POSTS_FILE = "seen_posts.json"
MAX_SEEN_POSTS_STORED = 2000  # oldest entries drop off after this many

# ── X (TWITTER) TOGGLE ──
# Set to True once TWITTERAPI_IO_KEY is set as a GitHub secret (see README).
X_ENABLED = True
