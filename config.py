"""
MoneyRadar Agent — Configuration
Edit this file to tune what the agent looks for. No code changes needed elsewhere.
"""


# ── SUBREDDITS TO SCAN ──
# Reddit is disabled in main.py (reddit_posts = []). Kept here so nothing that
# imports it breaks.
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
# Queries use X's OR grouping, so one query covers what used to take 3-4.
# Fewer queries = fewer API credits per run, same coverage.
# -filter:replies drops "drop your wallet below" reply spam.
# -"drop your" drops engagement-bait giveaways.
_X_EXCLUDE = '-bet -betting -casino -gambling -filter:replies -"drop your"'

# English (x_scanner currently adds lang:en to these)
X_SEARCH_QUERIES = [
    # cash offers
    f'("referral bonus" OR "refer and earn" OR "invite friends") (fintech OR remittance OR "cross border") {_X_EXCLUDE}',
    f'("referral bonus" OR "refer and earn" OR "invite friends") (usdt OR "crypto wallet" OR web3) {_X_EXCLUDE}',
    f'("registration bonus" OR "welcome bonus" OR "sign up bonus" OR "new users get") (exchange OR wallet OR crypto) {_X_EXCLUDE}',
    f'("instant cashback" OR "trading bonus" OR "deposit bonus") (naira OR crypto OR usdt) {_X_EXCLUDE}',
    f'("complete survey" OR "task reward" OR "claim reward") (usdc OR usdt OR wallet) {_X_EXCLUDE}',
    f'("bonus code" OR "promo code") ("send money" OR remittance OR "virtual card") {_X_EXCLUDE}',
    f'("sign up bonus" OR "new user") (polymarket OR solana) {_X_EXCLUDE}',
    # task airdrops
    f'airdrop ("connect x" OR "connect twitter" OR "connect wallet" OR "complete tasks") {_X_EXCLUDE}',
    f'airdrop (galxe OR zealy OR layer3 OR taskon) quest {_X_EXCLUDE}',
    f'airdrop ("create an account" OR "sign up" OR deposit) (exchange OR claim) {_X_EXCLUDE}',
    f'("airdrop is live" OR "claim is live" OR "eligibility checker" OR "claim now") {_X_EXCLUDE}',
    # points that convert to cash right away
    f'(points OR xp) ("convert to usdt" OR "redeem for usdt" OR "swap to usdt" OR "withdraw as usdt") {_X_EXCLUDE}',
    # KOL referral-app posts ("FREE $18 + $1 per ref", "enter code", "instant withdrawal")
    f'("refer friends" OR "per referral" OR "per ref" OR "referral code" OR "invite code") ("instant withdrawal" OR "withdraw instantly" OR "withdrawn directly" OR "instant payment" OR "withdraw to your wallet") {_X_EXCLUDE}',
    f'("enter code" OR "bind referral code" OR "use my code" OR "use code") (rewards OR bonus OR "free") (app OR wallet OR withdraw) {_X_EXCLUDE}',
    # free credit -> cash apps (Phygitals-style: ref credit -> open pack -> sell instantly)
    f'("open a pack" OR "open packs" OR "sell instantly" OR "instant sell" OR "sell back") (referral OR "per ref" OR "free" OR "sign up") {_X_EXCLUDE}',
    # naira — any app, especially new ones launching referral programs
    f'("just launched" OR "new app" OR "now live" OR "launching" OR "beta") (referral OR "refer and earn" OR "invite friends" OR "sign up bonus") (naira OR ngn OR nigeria OR nigerians) {_X_EXCLUDE}',
    f'("referral program" OR "refer and earn" OR "referral bonus" OR "per referral" OR "per invite") (naira OR ngn OR nigeria OR nigerians) {_X_EXCLUDE}',
    f'(naira OR ngn) ("sign up" OR "welcome bonus" OR bonus OR cashback OR "earn") (app OR withdraw OR bank) {_X_EXCLUDE}',
    f'(nigeria OR nigerians OR naija) ("free money" OR "earn money" OR "get paid" OR "instant payment") (app OR "sign up" OR referral) {_X_EXCLUDE}',
    f'("withdraw to your bank" OR "withdraw to bank" OR "free airtime" OR "free data") (app OR "sign up" OR referral OR earn) {_X_EXCLUDE}',
]

# Chinese — needs the updated x_scanner.py (uses lang:zh). Not used until then.
_X_EXCLUDE_ZH = '-博彩 -菠菜 -filter:replies'
X_SEARCH_QUERIES_ZH = [
    f'(空投 OR 撸毛) (任务 OR 领取 OR 白名单) {_X_EXCLUDE_ZH}',
    f'(零撸 OR 0撸) (空投 OR 项目) {_X_EXCLUDE_ZH}',
    f'(交易所 OR 钱包) (新用户 OR 注册) (奖励 OR 福利) (USDT OR U) {_X_EXCLUDE_ZH}',
    f'(邀请 OR 返佣) 奖励 USDT {_X_EXCLUDE_ZH}',
    f'积分 (兑换 OR 提现) USDT {_X_EXCLUDE_ZH}',
    f'(测试网 OR 交互) 空投 {_X_EXCLUDE_ZH}',
]

# Korean — needs the updated x_scanner.py (uses lang:ko). Not used until then.
_X_EXCLUDE_KO = '-filter:replies'
X_SEARCH_QUERIES_KO = [
    f'(에어드랍 OR 에어드롭) (이벤트 OR 퀘스트 OR 화이트리스트) {_X_EXCLUDE_KO}',
    f'거래소 (신규가입 OR 가입) (이벤트 OR 보상) (USDT OR 에어드랍) {_X_EXCLUDE_KO}',
    f'포인트 (전환 OR 교환) USDT {_X_EXCLUDE_KO}',
]

# One place for the scanner to loop over once it's updated.
X_SEARCH_QUERY_SETS = [
    {"lang": "en", "queries": X_SEARCH_QUERIES},
    {"lang": "zh", "queries": X_SEARCH_QUERIES_ZH},
    {"lang": "ko", "queries": X_SEARCH_QUERIES_KO},
]


# ── TELEGRAM CHANNELS (public t.me/s/<handle> preview, no login) ──
# A handle with no public preview just returns 0 posts and gets skipped.
# Check the per-channel counts in the Actions log after a few runs and
# prune the ones that never produce alerts.
# Exchange channels: only add handles you've confirmed on the exchange's own
# website — impersonation channels with near-identical handles are common.
TELEGRAM_ENABLED = True
TELEGRAM_CHANNELS = [
    # english — exchange campaigns (fixed-reward promos, new-user bonuses)
    "Bybit_Announcements",
    "binance_announcements",
    # english — airdrop aggregators
    "officialairdropalert",
    # chinese — airdrop / 撸毛 aggregators
    "kongtoufabu",
    "tglukongtou",
    "newsforbitcoin",
    # chinese — news (catches claim-checker openings, exchange campaigns)
    "wublock",
]
TELEGRAM_MAX_AGE_DAYS = 3   # ignore channel posts older than this


# ── ALERT TIERS ──
TIER_LABELS = {
    "cash": "💰 cash offer",
    "airdrop": "🪂 task airdrop",
    "points": "🎯 points → cash",
}
POINTS_TIER_ENABLED = True   # points/XP only alert if convertible to money right now


# ── KEYWORDS ──
# Matching is whole-word for latin text ("bet" no longer matches "better",
# "between" or "beta"). Chinese/Korean match as plain substrings.

# Cash-offer phrasing (post must match one of these, an airdrop keyword,
# or a points keyword)
OPPORTUNITY_KEYWORDS = [
    "referral", "refer a friend", "refer and earn", "invite code",
    "invite friend", "refer friend", "referral bonus", "referral code",
    "refer & earn", "sign up bonus", "signup bonus",
    "cashback", "instant cashback", "registration bonus", "welcome bonus",
    "deposit bonus", "trading bonus", "cash reward", "instant reward",
    "survey reward", "complete survey", "claim reward", "claim your",
    "bonus code", "task reward", "complete task", "quest reward",
    "sign up and get", "deposit and get", "new user", "new users get",
    "first deposit", "promo code",
    "refer friends", "per referral", "per ref", "enter code", "use my code",
    "use code", "instant withdrawal", "withdraw instantly", "instant payment",
    "claim your free", "free bonus", "bonus", "free airtime", "free data",
    "earn ₦", "get ₦", "free ₦",
    # chinese
    "邀请", "返佣", "注册奖励", "新用户", "新人", "福利", "奖励", "领取",
    # korean
    "추천인", "신규가입", "가입 이벤트", "보상", "이벤트",
]

# Task-airdrop phrasing. A match here skips the category check (an airdrop
# is crypto by definition) and doesn't need a stated amount.
AIRDROP_KEYWORDS = [
    "airdrop", "air drop", "testnet", "galxe", "zealy", "layer3", "taskon",
    "retroactive", "whitelist", "allowlist", "eligibility checker",
    "claim is live", "airdrop is live",
    # chinese
    "空投", "撸毛", "零撸", "0撸", "白名单", "测试网",
    # korean
    "에어드랍", "에어드롭", "화이트리스트",
]

# Points/XP — only counts when a conversion word is also present
POINTS_KEYWORDS = ["points", "xp", "credits", "积分", "포인트"]
CONVERSION_KEYWORDS = [
    "convert", "redeem", "swap to", "exchange for", "withdraw", "cash out",
    "兑换", "提现", "전환", "교환", "출금",
]

# Category (post must match one, unless it matched an airdrop keyword)
CATEGORY_KEYWORDS = [
    "fintech", "crypto", "wallet", "exchange", "remit", "remittance",
    "cross-border", "cross border", "usdt", "usdc", "bitcoin", "stablecoin",
    "defi", "web3", "virtual card", "neobank", "naira", "cash app", "p2p",
    "solana", "polymarket", "international transfer", "money transfer", "send money",
    "binance", "bybit", "okx", "bitget", "kucoin", "mexc", "gate.io", "bnb",
    "withdraw", "withdrawal", "withdrawn", "cash out",
    # naira / nigeria (any app — names below are just extra hits, not a limit)
    "nigeria", "nigerian", "nigerians", "naija", "bank account", "to your bank",
    "₦", "ngn", "opay", "palmpay", "moniepoint", "kuda", "paga", "chipper",
    "fairmoney", "piggyvest", "cowrywise", "risevest", "geegpay", "raenest",
    "cleva", "airtime", "bank transfer",
    # chinese
    "币", "钱包", "交易所", "链上", "加密", "web3",
    # korean
    "코인", "지갑", "거래소", "가상자산",
]

# ── FREE CREDIT THAT TURNS INTO CASH ──
# Apps that pay referral/signup rewards as in-app credit you can turn into
# withdrawable money — e.g. Phygitals: $1 per referral -> open a pack -> sell
# the card instantly for cash. True = alert on these (no deposit needed).
# False = skip anything pack / mystery-box based.
ALLOW_FREE_CHANCE_CREDIT = True


# ── EXCLUDE KEYWORDS (post is rejected if ANY of these appear) ──
# Betting/gambling and gift cards per explicit request, generic noise
# formats, plus wallet-drainer / scam tells.
EXCLUDE_KEYWORDS = [
    "bet", "betting", "casino", "gamble", "gambling", "gambler", "sportsbook",
    "wager", "odds", "parlay",
    "daily alpha", "giveaway list",
    "gift card", "giftcard", "gift-card",
    # scam tells
    "seed phrase", "recovery phrase", "private key", "drop your wallet",
    "drop your address", "guaranteed profit", "double your",
    "助记词", "私钥", "博彩", "菠菜", "付费推广",
    "시드 문구", "개인키", "도박",
]


# ── MINIMUM PAYOUT THRESHOLDS ──
MIN_NAIRA = 500
MIN_USD = 1        # also applies to USDT/USDC/"U"/美元/刀
MIN_KRW = 1500     # ~$1


# ── SCAN FREQUENCY ──
# Informational only — actual frequency is the cron in
# .github/workflows/scan.yml (every 2 hours: "17 */2 * * *").
SCAN_FREQUENCY_HOURS = 2


# ── DEDUPE ──
# Posts already alerted on are stored here so you don't get repeat pings.
SEEN_POSTS_FILE = "seen_posts.json"
MAX_SEEN_POSTS_STORED = 20000  # oldest entries drop off after this many (~6 days at current volume)


# ── X (TWITTER) TOGGLE ──
# Set to True once TWITTERAPI_IO_KEY is set as a GitHub secret (see README).
X_ENABLED = True
