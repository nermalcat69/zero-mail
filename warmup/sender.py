"""
sender.py — SMTP sending logic with retry, rate-limiting, and Turso logging.

send_one_email()  — sends a single email, retries once on failure, logs to Turso.
send_batch()      — sends N emails to the recipient pool with randomised delays.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from datetime import datetime, timedelta

import libsql_client

import db
from config import (
    GMAIL_APP_PASSWORD,
    GMAIL_USER,
    RECIPIENTS,
    RETRY_WAIT_SECONDS,
    SMTP_HOST,
    SMTP_PORT,
    RATE_LIMIT_MAX_IN_WINDOW,
    RATE_LIMIT_WINDOW_SECONDS,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    ERROR_LOG_FILE,
)
from email_composer import compose_email

logger = logging.getLogger(__name__)

# ─── Recipient rotation state ─────────────────────────────────────────────────
_recipient_index = 0


def _next_recipient() -> str:
    global _recipient_index
    recipient = RECIPIENTS[_recipient_index % len(RECIPIENTS)]
    _recipient_index += 1
    return recipient


# ─── Core SMTP send ──────────────────────────────────────────────────────────

def _smtp_send(msg) -> None:
    """Open an SMTP-over-TLS connection and send msg. Raises on failure."""
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(
            GMAIL_USER,
            msg["To"],
            msg.as_bytes(),
        )


# ─── Single email send with one retry ────────────────────────────────────────

async def send_one_email(
    client: libsql_client.Client,
    *,
    day_number: int,
    dry_run: bool = False,
    used_combinations: set[tuple[str, str]] | None = None,
    index: int = 1,
    total: int = 1,
) -> bool:
    """
    Compose and send one email.  Retries once after RETRY_WAIT_SECONDS on failure.

    Returns True on success, False on final failure.
    """
    recipient = _next_recipient()
    msg, subject, body_preview, email_type = compose_email(
        GMAIL_USER,
        recipient,
        used_combinations=used_combinations,
    )

    ts_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts_label}] Sending {index}/{total} → {recipient}")

    async def _attempt() -> bool:
        sent_at = datetime.now()
        status = "sent"
        error_msg = None
        try:
            if dry_run:
                time.sleep(0.3)  # simulate latency
                logger.info("[DRY RUN] Would have sent to %s: %s", recipient, subject)
            else:
                _smtp_send(msg)
        except smtplib.SMTPException as exc:
            status = "failed"
            error_msg = str(exc)
        except Exception as exc:
            status = "failed"
            error_msg = f"Unexpected error: {exc}"

        try:
            await db.log_email(
                client,
                sent_at=sent_at,
                recipient=recipient,
                subject=subject,
                body_preview=body_preview,
                status=status,
                error_message=error_msg,
                day_number=day_number,
                email_type=email_type,
            )
        except Exception as db_exc:
            _log_local_error(f"Could not log to Turso: {db_exc}")

        return status == "sent", error_msg

    success, err = await _attempt()

    if success:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] \u2713 Sent: \"{subject}\"")
        if used_combinations is not None:
            used_combinations.add((subject, body_preview))
        return True

    # One retry after a wait
    logger.warning("Send failed (%s). Retrying in %ds…", err, RETRY_WAIT_SECONDS)
    _log_local_error(f"Send failed to {recipient}: {err}. Retrying…")
    await asyncio.sleep(RETRY_WAIT_SECONDS)

    # Re-compose to get a fresh message object (same recipient slot)
    global _recipient_index
    _recipient_index -= 1  # stay on same recipient for retry
    msg, subject, body_preview, email_type = compose_email(
        GMAIL_USER,
        recipient,
        used_combinations=used_combinations,
    )
    _recipient_index += 1  # advance past it now

    success, err = await _attempt()
    if success:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] \u2713 Sent (retry): \"{subject}\"")
        if used_combinations is not None:
            used_combinations.add((subject, body_preview))
        return True

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] \u2717 Failed after retry → {recipient}: {err}")
    _log_local_error(f"Permanent failure sending to {recipient}: {err}")
    return False


# ─── Batch send with delays and rate-limiting ─────────────────────────────────

async def send_batch(
    client: libsql_client.Client,
    *,
    count: int,
    day_number: int,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Send `count` emails spaced with randomised delays (MIN_DELAY – MAX_DELAY seconds).
    Enforces the rate limit (max RATE_LIMIT_MAX_IN_WINDOW per 30-min window).

    Returns (sent_count, failed_count).
    """
    sent = 0
    failed = 0
    used_combinations: set[tuple[str, str]] = set()

    for i in range(1, count + 1):
        # ── Rate-limit check ─────────────────────────────────────────────────
        window_start = datetime.now() - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        in_window = await db.get_sends_in_window(
            client, window_start.isoformat()
        )
        if in_window >= RATE_LIMIT_MAX_IN_WINDOW:
            wait_secs = RATE_LIMIT_WINDOW_SECONDS - (
                datetime.now() - window_start
            ).seconds + 60
            logger.info(
                "Rate limit reached (%d in last 30 min). Waiting %ds.",
                in_window,
                wait_secs,
            )
            await asyncio.sleep(wait_secs)

        # ── Send ─────────────────────────────────────────────────────────────
        ok = await send_one_email(
            client,
            day_number=day_number,
            dry_run=dry_run,
            used_combinations=used_combinations,
            index=i,
            total=count,
        )
        if ok:
            sent += 1
        else:
            failed += 1

        # ── Randomised delay before next send (skip after last email) ────────
        if i < count:
            delay = _random_delay()
            logger.debug("Sleeping %ds before next send.", delay)
            await asyncio.sleep(delay)

    return sent, failed


# ─── Delay helpers ────────────────────────────────────────────────────────────

def _random_delay() -> int:
    """Return a random delay in seconds within the configured window."""
    import random
    return random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)


# ─── Local error logging ──────────────────────────────────────────────────────

def _log_local_error(message: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    try:
        with open(ERROR_LOG_FILE, "a") as fh:
            fh.write(f"[{ts}] SENDER: {message}\n")
    except OSError:
        pass
    logger.error(message)
