"""
db.py — Async Turso/libsql database client and all data operations.

All public functions are async and must be called with await (or via
asyncio.run() from synchronous contexts such as the scheduler).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

import libsql_client

from config import TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, ERROR_LOG_FILE

logger = logging.getLogger(__name__)

# ─── Schema DDL ───────────────────────────────────────────────────────────────

_CREATE_SENT_EMAILS = """
CREATE TABLE IF NOT EXISTS sent_emails (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at       TEXT    NOT NULL,
    recipient     TEXT    NOT NULL,
    subject       TEXT    NOT NULL,
    body_preview  TEXT,
    status        TEXT    NOT NULL DEFAULT 'sent',
    error_message TEXT,
    day_number    INTEGER NOT NULL,
    email_type    TEXT    NOT NULL DEFAULT 'plain'
)
"""

_CREATE_DAILY_STATS = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date         TEXT    PRIMARY KEY,
    day_number   INTEGER NOT NULL,
    target_count INTEGER NOT NULL DEFAULT 0,
    actual_sent  INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_WARMUP_META = """
CREATE TABLE IF NOT EXISTS warmup_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


# ─── Client Factory ───────────────────────────────────────────────────────────

def create_client() -> libsql_client.Client:
    """Create and return a Turso libsql client using the HTTP API."""
    # libsql:// triggers WebSocket transport which has 505 issues on Turso.
    # Rewrite to https:// so the library uses the stable HTTP API instead.
    url = TURSO_DATABASE_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return libsql_client.create_client(
        url=url,
        auth_token=TURSO_AUTH_TOKEN,
    )


# ─── Schema Initialisation ───────────────────────────────────────────────────

async def init_db(client: libsql_client.Client) -> None:
    """Create all tables if they don't already exist."""
    try:
        await client.batch([
            _CREATE_SENT_EMAILS,
            _CREATE_DAILY_STATS,
            _CREATE_WARMUP_META,
        ])
        logger.info("Database schema initialised.")
    except Exception as exc:
        _log_local_error(f"init_db failed: {exc}")
        raise


# ─── Warmup Meta (start date) ────────────────────────────────────────────────

async def get_warmup_start_date(client: libsql_client.Client) -> date | None:
    """Return the stored warmup start date, or None if not set."""
    try:
        rs = await client.execute(
            "SELECT value FROM warmup_meta WHERE key = ?",
            ["warmup_start_date"],
        )
        if rs.rows:
            return date.fromisoformat(str(rs.rows[0][0]))
    except Exception as exc:
        _log_local_error(f"get_warmup_start_date failed: {exc}")
    return None


async def set_warmup_start_date(
    client: libsql_client.Client, start: date
) -> None:
    """Persist the warmup start date (once, on first init)."""
    try:
        await client.execute(
            "INSERT OR IGNORE INTO warmup_meta (key, value) VALUES (?, ?)",
            ["warmup_start_date", start.isoformat()],
        )
    except Exception as exc:
        _log_local_error(f"set_warmup_start_date failed: {exc}")
        raise


# ─── Email Logging ────────────────────────────────────────────────────────────

async def log_email(
    client: libsql_client.Client,
    *,
    sent_at: datetime,
    recipient: str,
    subject: str,
    body_preview: str,
    status: str,
    error_message: str | None,
    day_number: int,
    email_type: str,
) -> None:
    """Insert one row into sent_emails."""
    try:
        await client.execute(
            """
            INSERT INTO sent_emails
                (sent_at, recipient, subject, body_preview,
                 status, error_message, day_number, email_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                sent_at.isoformat(),
                recipient,
                subject,
                body_preview[:100],
                status,
                error_message,
                day_number,
                email_type,
            ],
        )
    except Exception as exc:
        _log_local_error(f"log_email failed: {exc}")
        raise


# ─── Daily Stats ─────────────────────────────────────────────────────────────

async def update_daily_stats(
    client: libsql_client.Client,
    *,
    date_str: str,
    day_number: int,
    target_count: int,
    actual_sent: int,
    failed_count: int,
) -> None:
    """Upsert a row in daily_stats for the given date."""
    try:
        await client.execute(
            """
            INSERT INTO daily_stats (date, day_number, target_count, actual_sent, failed_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                day_number   = excluded.day_number,
                target_count = excluded.target_count,
                actual_sent  = excluded.actual_sent,
                failed_count = excluded.failed_count
            """,
            [date_str, day_number, target_count, actual_sent, failed_count],
        )
    except Exception as exc:
        _log_local_error(f"update_daily_stats failed: {exc}")
        raise


async def get_sent_today(client: libsql_client.Client, date_str: str) -> int:
    """Return the count of successfully sent emails on date_str (YYYY-MM-DD)."""
    try:
        rs = await client.execute(
            """
            SELECT COUNT(*) FROM sent_emails
            WHERE status = 'sent'
              AND sent_at LIKE ?
            """,
            [f"{date_str}%"],
        )
        return int(rs.rows[0][0]) if rs.rows else 0
    except Exception as exc:
        _log_local_error(f"get_sent_today failed: {exc}")
        return 0


async def get_failed_today(client: libsql_client.Client, date_str: str) -> int:
    """Return the count of failed sends on date_str."""
    try:
        rs = await client.execute(
            """
            SELECT COUNT(*) FROM sent_emails
            WHERE status = 'failed'
              AND sent_at LIKE ?
            """,
            [f"{date_str}%"],
        )
        return int(rs.rows[0][0]) if rs.rows else 0
    except Exception as exc:
        _log_local_error(f"get_failed_today failed: {exc}")
        return 0


# ─── Duplicate Detection ─────────────────────────────────────────────────────

async def combination_used_before(
    client: libsql_client.Client, subject: str, body_preview: str
) -> bool:
    """Return True if the exact subject+body_preview combo was already sent."""
    try:
        rs = await client.execute(
            """
            SELECT COUNT(*) FROM sent_emails
            WHERE subject = ? AND body_preview = ?
            """,
            [subject, body_preview[:100]],
        )
        return int(rs.rows[0][0]) > 0 if rs.rows else False
    except Exception as exc:
        _log_local_error(f"combination_used_before failed: {exc}")
        return False


# ─── Reporting ────────────────────────────────────────────────────────────────

async def get_all_stats(
    client: libsql_client.Client,
) -> list[dict[str, Any]]:
    """Return all rows from daily_stats, ordered by date."""
    try:
        rs = await client.execute(
            "SELECT date, day_number, target_count, actual_sent, failed_count "
            "FROM daily_stats ORDER BY date"
        )
        return [
            {
                "date": row[0],
                "day_number": row[1],
                "target_count": row[2],
                "actual_sent": row[3],
                "failed_count": row[4],
            }
            for row in rs.rows
        ]
    except Exception as exc:
        _log_local_error(f"get_all_stats failed: {exc}")
        return []


async def get_recent_errors(
    client: libsql_client.Client, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the most recent failed send rows."""
    try:
        rs = await client.execute(
            """
            SELECT sent_at, recipient, subject, error_message
            FROM sent_emails
            WHERE status = 'failed'
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            [limit],
        )
        return [
            {
                "sent_at": row[0],
                "recipient": row[1],
                "subject": row[2],
                "error_message": row[3],
            }
            for row in rs.rows
        ]
    except Exception as exc:
        _log_local_error(f"get_recent_errors failed: {exc}")
        return []


# ─── Reset ────────────────────────────────────────────────────────────────────

async def reset_all_data(client: libsql_client.Client) -> None:
    """Wipe all warmup data from Turso (full reset to Day 1)."""
    try:
        await client.batch([
            "DELETE FROM sent_emails",
            "DELETE FROM daily_stats",
            "DELETE FROM warmup_meta",
        ])
        logger.warning("All warmup data has been wiped from Turso.")
    except Exception as exc:
        _log_local_error(f"reset_all_data failed: {exc}")
        raise


# ─── Rate-limit helper ───────────────────────────────────────────────────────

async def get_sends_in_window(
    client: libsql_client.Client, since_iso: str
) -> int:
    """Return number of successful sends since since_iso timestamp."""
    try:
        rs = await client.execute(
            """
            SELECT COUNT(*) FROM sent_emails
            WHERE status = 'sent' AND sent_at >= ?
            """,
            [since_iso],
        )
        return int(rs.rows[0][0]) if rs.rows else 0
    except Exception as exc:
        _log_local_error(f"get_sends_in_window failed: {exc}")
        return 0


# ─── Local fallback logging ───────────────────────────────────────────────────

def _log_local_error(message: str) -> None:
    """Write a message to the local error log when Turso is unreachable."""
    ts = datetime.now().isoformat(timespec="seconds")
    try:
        with open(ERROR_LOG_FILE, "a") as fh:
            fh.write(f"[{ts}] {message}\n")
    except OSError:
        pass  # nothing more we can do
    logger.error(message)
