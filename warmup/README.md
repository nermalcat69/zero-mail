# Gmail Warmup Automation System

A production-ready Python system that gradually warms up a Gmail sending account from 0 to 30 emails/day over 28 days, avoiding spam filters through randomised timing, varied content, and cloud-backed logging via [Turso](https://turso.tech).

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Turso Setup](#2-turso-setup)
3. [Gmail App Password Setup](#3-gmail-app-password-setup)
4. [Installation](#4-installation)
5. [Configure .env](#5-configure-env)
6. [Usage](#6-usage)
7. [Ramp-Up Schedule](#7-ramp-up-schedule)
8. [How It Works](#8-how-it-works)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.9 or higher |
| Gmail account | With 2-Step Verification enabled |
| Turso account | Free tier is sufficient |

---

## 2. Turso Setup

Turso is the cloud database used to log all sends and track daily stats.

### Step 1 — Install the Turso CLI

```bash
curl -sSfL https://get.tur.so/install.sh | bash
```

Restart your shell (or run `source ~/.bashrc` / `source ~/.zshrc`).

### Step 2 — Log in

```bash
turso auth login
```

This opens a browser window. Sign in with GitHub or email.

### Step 3 — Create the database

```bash
turso db create gmail-warmup
```

### Step 4 — Get the database URL

```bash
turso db show gmail-warmup --url
```

Copy the output (starts with `libsql://`). This is your `TURSO_DATABASE_URL`.

### Step 5 — Generate an auth token

```bash
turso db tokens create gmail-warmup
```

Copy the long JWT string. This is your `TURSO_AUTH_TOKEN`.

### Step 6 — Paste both into `.env`

```
TURSO_DATABASE_URL=libsql://gmail-warmup-<your-org>.turso.io
TURSO_AUTH_TOKEN=eyJh...
```

---

## 3. Gmail App Password Setup

Google requires an **App Password** (not your main account password) for SMTP access.

### Step 1 — Enable 2-Step Verification

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Click **Security** in the left sidebar
3. Under *How you sign in to Google*, click **2-Step Verification**
4. Follow the prompts to enable it

### Step 2 — Create an App Password

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Sign in if prompted
3. In the *App name* field, type: `gmail-warmup`
4. Click **Create**
5. Google shows a 16-character password (format: `xxxx xxxx xxxx xxxx`)
6. Copy it immediately — it won't be shown again

### Step 3 — Add to `.env`

```
GMAIL_USER=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

Spaces in the app password are fine — the system strips them automatically.

> **Note:** If you don't see the App Passwords option, 2-Step Verification may not be fully enabled, or your account may be managed by Google Workspace (in which case an admin must allow App Passwords).

---

## 4. Installation

```bash
# Clone the repo
git clone https://github.com/your-org/mail-api-bulk-drafter.git
cd mail-api-bulk-drafter/warmup

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## 5. Configure .env

Copy the template and fill in your values:

```bash
cp .env.example .env
```

Open `.env` in your editor:

```dotenv
# Gmail
GMAIL_USER=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# Turso
TURSO_DATABASE_URL=libsql://gmail-warmup-<your-org>.turso.io
TURSO_AUTH_TOKEN=eyJh...
```

| Variable | Description |
|---|---|
| `GMAIL_USER` | The Gmail address being warmed up (used as sender) |
| `GMAIL_APP_PASSWORD` | 16-char App Password from Google |
| `TURSO_DATABASE_URL` | `libsql://` URL from `turso db show` |
| `TURSO_AUTH_TOKEN` | JWT token from `turso db tokens create` |

---

## 6. Usage

All commands are run from inside the `warmup/` directory.

### Initialise the database (first time only)

```bash
python main.py --init
```

Creates all Turso tables and records the warmup start date. Run this once before anything else.

### Run today's warmup

```bash
python main.py
```

Sends today's allotment of emails, spread randomly across business hours (8 AM–8 PM). The process blocks until all emails for the day are sent.

### Check status

```bash
python main.py --status
```

Prints current day, today's sends vs target, all-time totals, and any recent errors.

### Dry run (simulate without sending)

```bash
python main.py --dry-run
```

Goes through the full flow — composes emails, logs mock records to Turso — but does not connect to Gmail SMTP. Useful for testing configuration.

### Run as a daemon (continuous)

```bash
python main.py --daemon
```

Runs indefinitely. After completing each day's warmup, it sleeps until 8 AM the next morning and repeats. Ideal for running in a `screen`, `tmux`, or as a system service.

### Reset (restart from Day 1)

```bash
python main.py --reset
```

**Destructive.** Wipes all rows from `sent_emails`, `daily_stats`, and `warmup_meta` in Turso, and sets the start date to today. You will be asked to type `yes` to confirm.

---

## 7. Ramp-Up Schedule

| Phase | Days | Emails/Day |
|---|---|---|
| Week 1 | 1 – 7 | 3 – 5 |
| Week 2 | 8 – 14 | 8 – 12 |
| Week 3 | 15 – 21 | 15 – 20 |
| Week 4 | 22 – 28 | 25 – 30 |
| Steady state | 29+ | 30 (fixed) |

Sends are distributed randomly across 8 AM–8 PM local time, with 5–45 minute gaps between each email. No more than 5 emails are sent in any 30-minute window.

---

## 8. How It Works

```
main.py
  └── scheduler.run_daily_warmup()
        ├── config.get_target_count()      ← decides how many to send today
        ├── _plan_send_times()             ← randomises times across business hours
        └── sender.send_one_email() × N
              ├── email_composer.compose_email()   ← picks random template
              ├── _smtp_send()                     ← Gmail SMTP over TLS
              └── db.log_email()                   ← records to Turso
```

### Spam avoidance measures

- **Randomised timing** — no fixed intervals; each send time is drawn from a uniform distribution
- **No duplicate content** — subject+body combinations are checked against Turso history
- **Mixed content types** — 70% plain text, 30% HTML
- **Proper headers** — `Message-ID`, `Date`, `Reply-To`, `X-Mailer`, `List-Unsubscribe` (HTML only)
- **Spam keyword filter** — rejects any template containing: FREE, WINNER, CLICK NOW, GUARANTEED, CONGRATULATIONS, ACT NOW, URGENT
- **Rate limiting** — max 5 sends per 30-minute window
- **Recipient rotation** — evenly rotates across the 3 recipient addresses

---

## 9. Troubleshooting

### `SMTPAuthenticationError` / `Username and Password not accepted`

- Make sure you're using an **App Password**, not your main Gmail password.
- Verify 2-Step Verification is enabled on the account.
- Check `GMAIL_USER` matches the account the App Password was created for.
- If you copied the App Password with spaces, that's fine — or remove them.

### `TURSO_DATABASE_URL` / connection errors

- Run `turso db show gmail-warmup --url` again and ensure the URL in `.env` starts with `libsql://`.
- Check your `TURSO_AUTH_TOKEN` is not expired. Regenerate with `turso db tokens create gmail-warmup`.
- Turso free tier has connection limits — if you see rate errors, wait a minute and retry.

### Emails landing in spam

- Give the warmup time — the ramp-up is designed for gradual trust building.
- Ask the recipient(s) to mark the warmup emails as **Not Spam** and add your address to their contacts.
- Avoid making rapid config changes mid-warmup.
- Ensure your Gmail account has a profile picture and complete profile.

### `ModuleNotFoundError`

- Make sure you activated the virtual environment: `source .venv/bin/activate`
- Re-run: `pip install -r requirements.txt`

### Warmup stopped mid-day

Running `python main.py` again is safe — the system queries Turso for how many emails were already sent today and only sends the remainder.

### Checking raw logs

Errors are written to `warmup_errors.log` in the `warmup/` directory alongside the Turso records.

---

## File Reference

```
warmup/
├── .env.example        Template for all 4 environment variables
├── requirements.txt    pip dependencies
├── config.py           Constants, schedule, recipient list
├── email_composer.py   20 subject + 20 body templates, MIME composition
├── sender.py           SMTP logic, retry, rate limiting
├── scheduler.py        Daily orchestration, send-time planning, reporting
├── db.py               Async Turso client and all DB operations
├── main.py             CLI entry point (--init, --status, --dry-run, --reset, --daemon)
└── README.md           This file
```
