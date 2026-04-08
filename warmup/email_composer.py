"""
email_composer.py — Generates varied, realistic email content.

- 15+ subject line templates
- 15+ body templates covering multiple topics
- 70% plain text / 30% HTML mix
- Recipient first-name personalisation
- Spam keyword rejection
- Duplicate combination detection (via Turso)
"""

from __future__ import annotations

import random
import re
import string
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from config import (
    GMAIL_USER,
    HTML_EMAIL_PROBABILITY,
    SPAM_KEYWORDS,
)

# ─── Signature ────────────────────────────────────────────────────────────────

SIGNATURE_PLAIN = """
--
Arjun Aditya
Founder, Graycup
arjun@graycup.org
"""

SIGNATURE_HTML = """
<br><br>
<table cellpadding="0" cellspacing="0" style="font-family:Arial,sans-serif;font-size:13px;color:#555">
  <tr><td><strong>Arjun Aditya</strong></td></tr>
  <tr><td>Founder, Graycup</td></tr>
  <tr><td><a href="mailto:arjun@graycup.org">arjun@graycup.org</a></td></tr>
</table>
"""

# ─── Subject Templates (20) ───────────────────────────────────────────────────

SUBJECT_TEMPLATES = [
    "Quick question about the report",
    "Following up from last week",
    "Thoughts on the new proposal?",
    "Checking in — how's everything going?",
    "Re: our conversation yesterday",
    "A resource I thought you'd find useful",
    "Feedback request on the draft",
    "Weekend plans — are you around?",
    "Project update for {first_name}",
    "Meeting notes from Tuesday",
    "Article you might enjoy reading",
    "Let me know what you think",
    "Brief update on the timeline",
    "Looping you in on this",
    "Question before our next call",
    "Sharing something I found helpful",
    "Follow-up: next steps",
    "Just wanted to touch base",
    "Interesting read — passing it along",
    "Recap from our discussion",
]

# ─── Body Templates (20) ─────────────────────────────────────────────────────

BODY_TEMPLATES = [
    # 1 — Project update
    """\
Hey{name_part},

Just wanted to send over a quick update on where things stand with the project. \
We made some solid progress this week and the main blockers from last sprint have \
been resolved. The timeline is still on track for the end of the month.

Let me know if you need anything from my end before our next sync.

{sig}""",

    # 2 — Casual check-in
    """\
Hi{name_part},

Hope your week is going well. I was thinking about our last conversation and \
wanted to follow up to see how things have been progressing on your end. \
No rush at all — just checking in.

Feel free to reach out whenever you get a chance.

{sig}""",

    # 3 — Article share
    """\
Hi{name_part},

I came across an article this morning that I thought you'd find interesting. \
It covers some of the themes we were discussing around scalable infrastructure \
and distributed systems. The author makes a few points that I hadn't considered before.

Wanted to pass it along in case it's useful. Let me know what you think if you get \
a chance to read it.

{sig}""",

    # 4 — Meeting follow-up
    """\
Hey{name_part},

Thanks for taking the time to meet earlier this week. I really appreciated the \
discussion — there were a lot of useful ideas that came out of it. I've jotted \
down some notes on the key action items we agreed on.

Happy to share those if it would be helpful. Just let me know.

{sig}""",

    # 5 — Feedback request
    """\
Hi{name_part},

I've been working on a draft proposal and would really value your perspective on \
it before I share it more broadly. You always have a sharp eye for this kind of \
thing, so your feedback would mean a lot.

If you have 10–15 minutes this week, I'd love to walk you through it. Or I can \
just send the doc over if that's easier.

{sig}""",

    # 6 — Weekend plans
    """\
Hey{name_part},

Quick one — are you free this weekend? A few of us were thinking of getting \
together on Saturday afternoon if you're around. Would be great to catch up \
outside of the usual work context.

Let me know either way, no worries if you're busy.

{sig}""",

    # 7 — Resource sharing
    """\
Hi{name_part},

I wanted to share something that's been really helpful for me lately. It's a \
framework for structuring complex decisions that I picked up from a talk I \
attended last week. I think it could be useful for the kind of work you're doing.

Happy to chat through it if you're curious.

{sig}""",

    # 8 — Thank you note
    """\
Hi{name_part},

I just wanted to take a moment to say thank you for all the help recently. \
It genuinely made a difference and didn't go unnoticed. I really appreciate \
you taking the time when I know you're juggling a lot.

Looking forward to returning the favour whenever I can.

{sig}""",

    # 9 — Timeline update
    """\
Hey{name_part},

Wanted to give you a brief heads-up on where the timeline stands. We hit a \
minor delay on one of the dependencies, so we've shifted the target date by \
about a week. Everything else is proceeding as planned.

I'll keep you posted as things develop. Let me know if you have any questions.

{sig}""",

    # 10 — Loop-in
    """\
Hi{name_part},

I wanted to loop you in on something that came up today. It's related to the \
work we've been coordinating on and I think your input would be really valuable \
before we move forward.

Can you take a look when you get a moment and let me know your thoughts?

{sig}""",

    # 11 — Pre-call question
    """\
Hi{name_part},

Before our call next week I wanted to send over a quick question. I've been \
reviewing the materials we went over last time and there's one area I'd like \
to make sure we cover in depth.

Would it be possible to add about 10 extra minutes to the agenda for that?

{sig}""",

    # 12 — Meeting notes
    """\
Hey{name_part},

Here are a few notes from Tuesday's meeting that I wanted to share with you. \
We covered the roadmap priorities, resource allocation for Q2, and a few open \
questions around vendor contracts.

Let me know if I missed anything or if you'd like to add to the notes.

{sig}""",

    # 13 — Passing along an insight
    """\
Hi{name_part},

I was reading through some research this morning and came across a section that \
directly relates to something we talked about a few weeks ago. I thought it was \
worth passing along.

It's a pretty quick read — maybe 5 minutes. Let me know what you think.

{sig}""",

    # 14 — Touch base
    """\
Hey{name_part},

Just wanted to touch base and see how things are going. It's been a busy few \
weeks and I realise we haven't had a proper chance to catch up. \
Hope everything's going well on your end.

Always happy to jump on a quick call if there's anything useful we can discuss.

{sig}""",

    # 15 — Recap
    """\
Hi{name_part},

Following our discussion yesterday, I wanted to send a quick recap of the key \
points we landed on. I think we made good progress and the direction feels clear.

Let me know if you see anything that needs revisiting before we proceed.

{sig}""",

    # 16 — Intro / connecting dots
    """\
Hey{name_part},

Something came up today that made me think of you. I've been exploring a new \
approach to the problem we discussed and I think there might be a really \
interesting angle worth pursuing.

Would love to get your initial reaction if you have a few minutes this week.

{sig}""",

    # 17 — Soft check-in after silence
    """\
Hi{name_part},

I know it's been a little while since we last connected, so I just wanted to \
reach out and see how you're doing. No agenda — just checking in.

Hope things are going well. Feel free to drop me a line whenever.

{sig}""",

    # 18 — Positive reinforcement
    """\
Hey{name_part},

I saw the work you put out recently and I just wanted to say — really impressive. \
It's clear a lot of thought went into it and the output speaks for itself. \
Genuinely well done.

Looking forward to seeing what comes next.

{sig}""",

    # 19 — Sharing a tool/resource
    """\
Hi{name_part},

I've been using a new tool for the past couple of weeks that's made a noticeable \
difference to how I manage my workflow. I thought you might find it useful too \
given the kind of projects you're working on.

Happy to give you a quick walkthrough if you're interested.

{sig}""",

    # 20 — Gentle nudge / reminder
    """\
Hey{name_part},

Just a gentle nudge on this — I know it probably fell through the cracks given \
everything that's been going on. No pressure at all, just wanted to make sure \
it was still on your radar whenever you get a moment.

Thanks as always.

{sig}""",
]

# ─── HTML Body Wrappers ───────────────────────────────────────────────────────

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;max-width:620px;margin:0 auto;padding:20px">
{body_html}
</body>
</html>
"""


def _plain_to_html(plain: str) -> str:
    """Convert a plain-text body to minimal HTML paragraphs."""
    # Replace the plain signature separator with an HTML one
    plain = plain.replace(SIGNATURE_PLAIN, SIGNATURE_HTML)
    lines = plain.strip().split("\n")
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            html_lines.append(f"<p>{stripped}</p>")
        else:
            html_lines.append("<br>")
    return _HTML_WRAPPER.format(body_html="\n".join(html_lines))


# ─── Helper: first name from email ───────────────────────────────────────────

def _first_name(email: str) -> str:
    """
    Extract a capitalised first name from an email address.
    e.g. "arjuntaditya14@gmail.com" → "Arjun"  (strips digits, takes first word)
    """
    local = email.split("@")[0]
    # Strip trailing digits
    local = re.sub(r"\d+$", "", local)
    # Split on common separators
    parts = re.split(r"[.\-_]", local)
    name = parts[0].capitalize() if parts else local.capitalize()
    return name if name else "there"


# ─── Spam keyword check ───────────────────────────────────────────────────────

def _contains_spam_keywords(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SPAM_KEYWORDS)


# ─── Public API ──────────────────────────────────────────────────────────────

def compose_email(
    sender: str,
    recipient: str,
    *,
    force_plain: bool = False,
    used_combinations: set[tuple[str, str]] | None = None,
) -> tuple[MIMEMultipart, str, str, str]:
    """
    Build a MIME message ready to send via SMTP.

    Returns:
        (msg, subject, body_preview, email_type)
        where email_type is 'plain' or 'html'.
    """
    first_name = _first_name(recipient)
    # 50% chance to include first name personalisation
    name_part = f" {first_name}" if random.random() < 0.5 else ""

    max_attempts = 30
    for _ in range(max_attempts):
        subject_tmpl = random.choice(SUBJECT_TEMPLATES)
        body_tmpl = random.choice(BODY_TEMPLATES)

        subject = subject_tmpl.format(first_name=first_name)
        plain_body = body_tmpl.format(
            name_part=name_part,
            sig=SIGNATURE_PLAIN,
        )
        body_preview = plain_body[:100]

        # Reject spam keywords
        if _contains_spam_keywords(subject) or _contains_spam_keywords(plain_body):
            continue

        # Avoid duplicate subject+body combos
        combo = (subject, body_preview)
        if used_combinations is not None and combo in used_combinations:
            continue

        break
    else:
        # Fallback: append a unique token to force a fresh combo
        subject = f"Quick update [{_random_token()}]"
        plain_body = (
            f"Hi{name_part},\n\nJust a quick note to check in. "
            "Hope everything is going well on your end.\n"
            + SIGNATURE_PLAIN
        )
        body_preview = plain_body[:100]

    # Decide content type
    use_html = (not force_plain) and (random.random() < HTML_EMAIL_PROBABILITY)
    email_type = "html" if use_html else "plain"

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg["Reply-To"] = sender
    msg["X-Mailer"] = "Python/warmup-mailer"

    if use_html:
        html_body = _plain_to_html(plain_body)
        msg["List-Unsubscribe"] = f"<mailto:{sender}?subject=unsubscribe>"
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))

    return msg, subject, body_preview, email_type


def _random_token(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
