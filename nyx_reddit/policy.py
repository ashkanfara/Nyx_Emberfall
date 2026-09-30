"""House policy for the Nyx Reddit agent.

These are OUR limits, deliberately stricter than any subreddit's. Reddit's own
rules are recorded per community in data/communities.json; the gate enforces
whichever is stricter.
"""

# Every one of these must be verified in a normal browser before a community can
# be classified as approved. Keys map to structured fields the gate reads.
REQUIRED_RULE_FIELDS = {
    "ai_content": ["allowed", "disclosure_required", "disclosure_format"],
    "self_promotion": ["mode"],            # "allowed" | "ratio" | "promo_thread_only" | "none"
    "nsfw": ["allowed"],
    "links": ["external_allowed", "fanvue_allowed"],
    "account_minimums": ["min_age_days", "min_karma"],  # null = not published by the sub
    "flair": ["required", "options"],
    "frequency": ["max_posts", "per_days"],  # null = no published limit
}

SELF_PROMO_MODES = {"allowed", "ratio", "promo_thread_only", "none"}

STATUSES = {
    "unverified",
    "approved_native",
    "approved_promo_thread",
    "observe_only",
    "excluded",
}

RULE_MAX_AGE_DAYS = 14          # re-verify rules if older than this at gate time
ACCOUNT_SNAPSHOT_MAX_AGE_DAYS = 7

HOUSE_MIN_ACCOUNT_AGE_DAYS = 30
HOUSE_MIN_KARMA = 100
WARMUP_MIN_COMMENTS = 10
WARMUP_MIN_SPAN_DAYS = 14

TEST_WINDOW_DAYS = 30
TEST_MAX_POSTS = 3
MIN_DAYS_BETWEEN_POSTS = 7
NEGATIVE_UPVOTE_RATIO = 0.5
MAX_DRAFT_SIMILARITY = 0.5      # Jaccard on word sets between drafts

TITLE_MAX_CHARS = 300           # Reddit hard limit
MAX_IMAGES = 20

# Words that make a post read as a funnel. Blocked unless a verified rule allows
# it AND the draft is a promo-thread unit.
PROMO_TERMS = [
    "fanvue", "onlyfans", "link in bio", "subscribe", "dm me", "dm for",
    "exclusive content", "spicy", "uncensored", "discount", "promo code",
]
FANVUE_DOMAINS = ["fanvue.com", "fanvue.co", "fnvu"]

# Claims that would present Nyx as a real person.
REAL_PERSON_CLAIMS = [
    "i'm a real", "im a real", "real girl", "not ai", "not an ai",
    "100% real", "no filter", "irl pic", "selfie of me",
]

