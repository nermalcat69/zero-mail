"""
main.py — Entry point for the Gmail warmup system.

Usage:
  python main.py              # Run today's warmup (blocks until complete)
  python main.py --init       # Initialise Turso schema only
  python main.py --status     # Show current warmup status and exit
  python main.py --dry-run    # Simulate sends; still writes mock records to Turso
  python main.py --reset      # Wipe all Turso data and restart from Day 1
  python main.py --daemon     # Run as a daily daemon (loops indefinitely)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, datetime, timedelta

import libsql_client

import db
import scheduler
from config import (
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
    WARMUP_START_DATE,
    validate_env,
)

# ─── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("warmup_errors.log"),
    ],
)
logger = logging.getLogger(__name__)


# ─── Argument parser ─────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gmail Warmup System",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--init",
        action="store_true",
        help="Initialise Turso database schema and exit",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Show warmup status and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate sends without actually sending email",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Wipe all Turso warmup data and restart from Day 1",
    )
    p.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously, triggering daily warmup each morning",
    )
    return p.parse_args()


# ─── Async main ──────────────────────────────────────────────────────────────

async def _async_main(args: argparse.Namespace) -> None:
    # Validate env vars
    missing = validate_env()
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    # Create Turso client
    client = db.create_client()

    try:
        # ── --init ────────────────────────────────────────────────────────────
        if args.init:
            print("Initialising Turso database schema…")
            await db.init_db(client)
            start = await db.get_warmup_start_date(client)
            if start is None:
                start = date.today()
                await db.set_warmup_start_date(client, start)
                print(f"Warmup start date set to {start.isoformat()}")
            else:
                print(f"Warmup start date already set: {start.isoformat()}")
            print("Done. Run `python main.py` to start warming up.")
            return

        # ── --reset ───────────────────────────────────────────────────────────
        if args.reset:
            confirm = input(
                "This will WIPE all warmup data from Turso and restart from Day 1.\n"
                "Type 'yes' to confirm: "
            ).strip()
            if confirm.lower() != "yes":
                print("Aborted.")
                return
            await db.init_db(client)
            await db.reset_all_data(client)
            new_start = date.today()
            await db.set_warmup_start_date(client, new_start)
            print(f"Reset complete. New warmup start date: {new_start.isoformat()}")
            return

        # ── Load start date ───────────────────────────────────────────────────
        await db.init_db(client)
        start_date = await db.get_warmup_start_date(client)
        if start_date is None:
            # First run without --init: set start date now
            start_date = date.today()
            await db.set_warmup_start_date(client, start_date)
            print(f"First run — warmup start date set to {start_date.isoformat()}")

        # ── --status ──────────────────────────────────────────────────────────
        if args.status:
            await scheduler.print_status(client, start_date)
            return

        # ── Single run or dry-run ─────────────────────────────────────────────
        if not args.daemon:
            await scheduler.run_daily_warmup(
                client,
                start_date=start_date,
                dry_run=args.dry_run,
            )
            return

        # ── Daemon mode ───────────────────────────────────────────────────────
        print("Running in daemon mode. Press Ctrl+C to stop.")
        while True:
            await scheduler.run_daily_warmup(
                client,
                start_date=start_date,
                dry_run=args.dry_run,
            )
            # Sleep until next business day start
            tomorrow = _next_run_time()
            now = datetime.now()
            wait_secs = (tomorrow - now).total_seconds()
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{ts}] Next run scheduled for "
                f"{tomorrow.strftime('%Y-%m-%d %H:%M:%S')} "
                f"({wait_secs/3600:.1f}h from now)"
            )
            await asyncio.sleep(wait_secs)

    finally:
        await client.close()


def _next_run_time() -> datetime:
    """Return the datetime for the next day's business hour start."""
    tomorrow = date.today() + timedelta(days=1)
    return datetime(
        tomorrow.year,
        tomorrow.month,
        tomorrow.day,
        BUSINESS_HOUR_START,
        0,
        0,
    )


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()
