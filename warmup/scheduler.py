"""
scheduler.py — Daily ramp-up orchestration and send scheduling.

run_daily_warmup() is the main coroutine called once per day.
It:
  1. Calculates the current warmup day and target count.
  2. Determines randomised send times spread across business hours.
  3. Waits until each scheduled time, then fires send_batch().
  4. Writes a daily summary to Turso and prints it to the console.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timedelta

import libsql_client

import db
import sender
from config import (
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    get_day_number,
    get_target_count,
)

logger = logging.getLogger(__name__)


# ─── Business-hours send-time planner ────────────────────────────────────────

def _plan_send_times(count: int, base_date: date) -> list[datetime]:
    """
    Return `count` datetime objects spread randomly across business hours on
    base_date, with at least MIN_DELAY_SECONDS gap between each.
    """
    business_seconds = (BUSINESS_HOUR_END - BUSINESS_HOUR_START) * 3600
    min_needed = (count - 1) * MIN_DELAY_SECONDS
    if min_needed >= business_seconds:
        # Fallback: space evenly if window is too tight
        step = business_seconds // count
        offsets = [i * step for i in range(count)]
    else:
        offsets = sorted(random.sample(range(business_seconds), count))
        # Enforce minimum gap
        for i in range(1, len(offsets)):
            if offsets[i] - offsets[i - 1] < MIN_DELAY_SECONDS:
                offsets[i] = offsets[i - 1] + MIN_DELAY_SECONDS

    start_of_business = datetime(
        base_date.year,
        base_date.month,
        base_date.day,
        BUSINESS_HOUR_START,
        0,
        0,
    )
    return [start_of_business + timedelta(seconds=off) for off in offsets]


# ─── Wait until a target datetime ────────────────────────────────────────────

async def _wait_until(target: datetime) -> None:
    """Sleep until target datetime (skips if already past)."""
    now = datetime.now()
    delta = (target - now).total_seconds()
    if delta > 0:
        logger.debug("Waiting %.0fs until %s", delta, target.strftime("%H:%M:%S"))
        await asyncio.sleep(delta)


# ─── Daily warmup run ─────────────────────────────────────────────────────────

async def run_daily_warmup(
    client: libsql_client.Client,
    *,
    start_date: date,
    dry_run: bool = False,
) -> None:
    """
    Execute one full day of warmup:
      - Determine today's day number and target count.
      - Schedule sends across business hours.
      - Log results to Turso.
      - Print console summary.
    """
    today = date.today()
    date_str = today.isoformat()
    day_number = (today - start_date).days + 1
    target = get_target_count(day_number)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_label = "connected \u2713" if client else "unavailable \u2717"
    print(
        f"[{ts}] Day {day_number} | Target: {target} emails | Turso: {db_label}"
    )

    # ── Check what's already been sent today (resumability) ──────────────────
    already_sent = await db.get_sent_today(client, date_str)
    remaining = target - already_sent
    if remaining <= 0:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
              f"Target already reached ({already_sent}/{target}). Skipping.")
        return

    if already_sent > 0:
        print(f"Resuming: {already_sent} already sent today, {remaining} remaining.")

    # ── Plan send times for remaining emails ──────────────────────────────────
    send_times = _plan_send_times(remaining, today)

    # ── Fire each send at its scheduled time ──────────────────────────────────
    total_sent = already_sent
    total_failed = 0

    for send_time in send_times:
        await _wait_until(send_time)
        ok = await sender.send_one_email(
            client,
            day_number=day_number,
            dry_run=dry_run,
            index=total_sent - already_sent + 1,
            total=target,
        )
        if ok:
            total_sent += 1
        else:
            total_failed += 1

    # ── Update Turso daily stats ──────────────────────────────────────────────
    try:
        await db.update_daily_stats(
            client,
            date_str=date_str,
            day_number=day_number,
            target_count=target,
            actual_sent=total_sent,
            failed_count=total_failed,
        )
    except Exception as exc:
        logger.error("Could not update daily_stats: %s", exc)

    # ── Console summary ───────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fail_rate = (
        f"{total_failed} failed"
        if total_failed
        else "0 failed"
    )
    print(
        f"[{ts}] Daily summary: {total_sent}/{target} sent \u00b7 "
        f"{fail_rate} \u00b7 logged to Turso \u2713"
    )

    # ── Weekly ASCII chart (every 7 days) ─────────────────────────────────────
    if day_number % 7 == 0:
        await _print_weekly_chart(client, day_number)


# ─── Weekly ASCII chart ───────────────────────────────────────────────────────

async def _print_weekly_chart(
    client: libsql_client.Client, current_day: int
) -> None:
    stats = await db.get_all_stats(client)
    if not stats:
        return

    print("\n" + "─" * 52)
    print(f"  Warmup Progress — through Day {current_day}")
    print("─" * 52)
    print(f"  {'Date':<12} {'Day':>4} {'Target':>7} {'Sent':>6} {'Bar'}")
    print("─" * 52)

    max_count = max((r["target_count"] for r in stats), default=30)

    for row in stats:
        bar_len = int((row["actual_sent"] / max(max_count, 1)) * 20)
        bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
        print(
            f"  {row['date']:<12} {row['day_number']:>4} "
            f"{row['target_count']:>7} {row['actual_sent']:>6}  {bar}"
        )
    print("─" * 52 + "\n")


# ─── Status report ───────────────────────────────────────────────────────────

async def print_status(
    client: libsql_client.Client, start_date: date
) -> None:
    """Print current warmup status to stdout."""
    today = date.today()
    date_str = today.isoformat()
    day_number = (today - start_date).days + 1
    target = get_target_count(day_number)
    sent_today = await db.get_sent_today(client, date_str)
    failed_today = await db.get_failed_today(client, date_str)
    all_stats = await db.get_all_stats(client)

    total_sent_all = sum(r["actual_sent"] for r in all_stats)
    total_failed_all = sum(r["failed_count"] for r in all_stats)

    print("\n" + "═" * 52)
    print("  Gmail Warmup — Status Report")
    print("═" * 52)
    print(f"  Start date   : {start_date.isoformat()}")
    print(f"  Today        : {date_str}  (Day {day_number})")
    print(f"  Today target : {target}")
    print(f"  Sent today   : {sent_today}")
    print(f"  Failed today : {failed_today}")
    print(f"  All-time sent: {total_sent_all}")
    print(f"  All-time fail: {total_failed_all}")
    print("═" * 52)

    if day_number <= 28:
        days_left = 29 - day_number
        print(f"  Warmup phase: {days_left} day(s) until steady state (30/day)")
    else:
        print("  Warmup complete — running at steady state (30/day)")

    recent_errors = await db.get_recent_errors(client, limit=5)
    if recent_errors:
        print("\n  Recent errors:")
        for e in recent_errors:
            print(f"    [{e['sent_at']}] {e['recipient']} — {e['error_message']}")
    print()
