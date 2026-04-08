"""
config.py — Central configuration for the Gmail warmup system.
All constants, ramp-up schedule, and recipient pool live here.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# ─── Gmail Credentials ────────────────────────────────────────────────────────
GMAIL_USER: str = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# ─── Turso Credentials ────────────────────────────────────────────────────────
TURSO_DATABASE_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# ─── Recipient Pool ───────────────────────────────────────────────────────────
RECIPIENTS = [
    "arjuntaditya14@gmail.com",
    "office@graycup.org",
    "internet3.sh@gmail.com",
]

# ─── Warmup Ramp-Up Schedule ─────────────────────────────────────────────────
# Each tuple: (day_start, day_end, min_emails, max_emails)
RAMP_UP_SCHEDULE = [
    (1,  7,  3,  5),   # Week 1
    (8,  14, 8,  12),  # Week 2
    (15, 21, 15, 20),  # Week 3
    (22, 28, 25, 30),  # Week 4
]

# After day 28: always send exactly 30 per day
POST_WARMUP_COUNT = 30

# ─── Timing Constraints ───────────────────────────────────────────────────────
BUSINESS_HOUR_START = 8   # 8 AM local time
BUSINESS_HOUR_END   = 20  # 8 PM local time

MIN_DELAY_SECONDS = 5 * 60    # 5 minutes
MAX_DELAY_SECONDS = 45 * 60   # 45 minutes

# Rate limit: no more than this many emails in any 30-minute window
RATE_LIMIT_WINDOW_SECONDS = 30 * 60
RATE_LIMIT_MAX_IN_WINDOW  = 5

# Retry settings
RETRY_WAIT_SECONDS = 2 * 60   # 2 minutes before one retry on failure

# ─── Email Mix ────────────────────────────────────────────────────────────────
# Probability that a given email will be HTML (vs plain text)
HTML_EMAIL_PROBABILITY = 0.30   # 30% HTML, 70% plain

# ─── Spam Keywords to Reject ─────────────────────────────────────────────────
SPAM_KEYWORDS = [
    "free",
    "winner",
    "click now",
    "guaranteed",
    "congratulations",
    "act now",
    "urgent",
]

# ─── Warmup Start Date ────────────────────────────────────────────────────────
# Stored in DB on first init; falls back to today if not found.
# Override via environment variable WARMUP_START_DATE (YYYY-MM-DD) if needed.
_env_start = os.getenv("WARMUP_START_DATE", "")
WARMUP_START_DATE: date = (
    date.fromisoformat(_env_start) if _env_start else date.today()
)

# ─── Logging ─────────────────────────────────────────────────────────────────
ERROR_LOG_FILE = "warmup_errors.log"


def get_day_number(start_date: date | None = None) -> int:
    """Return the current warmup day number (1-indexed from start_date)."""
    base = start_date or WARMUP_START_DATE
    return (date.today() - base).days + 1


def get_target_count(day_number: int) -> int:
    """Return the target email count for the given warmup day."""
    import random
    for (d_start, d_end, mn, mx) in RAMP_UP_SCHEDULE:
        if d_start <= day_number <= d_end:
            return random.randint(mn, mx)
    # Day 29+: maintain steady state
    return POST_WARMUP_COUNT


def validate_env() -> list[str]:
    """Return a list of missing required environment variables."""
    missing = []
    for var, val in [
        ("GMAIL_USER", GMAIL_USER),
        ("GMAIL_APP_PASSWORD", GMAIL_APP_PASSWORD),
        ("TURSO_DATABASE_URL", TURSO_DATABASE_URL),
        ("TURSO_AUTH_TOKEN", TURSO_AUTH_TOKEN),
    ]:
        if not val:
            missing.append(var)
    return missing
