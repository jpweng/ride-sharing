"""
The contact form (frontend/contact.html, docs/contact.md): a visitor's
message is emailed to the team. Nothing of it is kept on the site.

  - Where it goes: the address in the CONTACT_EMAIL environment variable.
    Unset, the form is switched off: the page says so, the API answers 503.
  - The email's headers:
      From      the site's own sender (EMAIL_FROM), as for every email: the
                only address the server may send from, and the one SPF, DKIM
                and DMARC vouch for. Never the visitor's address, which
                receiving servers would reject or mark as spam.
      To        CONTACT_EMAIL.
      Reply-To  the visitor's address, so that answering the email answers
                the visitor.
      Subject   starts with SUBJECT_PREFIX, to filter and sort on, then the
                visitor's name.
  - Who may write: anyone, signed in or not (someone who can't sign in must
    be able to say so). The email says which account wrote, if any.
  - Limits, since anyone can make the site send an email: a few messages
    per IP address (PER_IP), and PER_DAY for all visitors together, which
    is what a mailbox and the sending quota can take. Counted in memory:
    the app runs as one process, and a restart only resets the count.
  - A field people don't see ("website") catches the simplest robots: a
    message with it filled in is answered like any other and dropped.
"""
import logging
import os
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

import auth
import mailer
from api import api_error

logger = logging.getLogger("uvicorn.error")

SUBJECT_PREFIX = "[Ride and Rail contact]"
MAX_NAME_CHARS = 100
MAX_MESSAGE_CHARS = 4000

# Messages per IP address, and for all visitors together: (window, max).
PER_IP = [(timedelta(minutes=10), 3), (timedelta(days=1), 10)]
PER_DAY = 200

# When each message was sent and from which IP address, for the limits;
# entries older than a day are dropped.
_sent: deque[tuple[datetime, str | None]] = deque()
_lock = threading.Lock()


def recipient() -> str | None:
    """Where messages go, or None when the form is switched off."""
    to = (os.environ.get("CONTACT_EMAIL") or "").strip()
    if not to:
        return None
    if not auth.EMAIL_RE.match(to):
        # A mistyped setting must not break the site: the form stays off.
        logger.warning("CONTACT_EMAIL isn't an email address: the contact form is off")
        return None
    return to


def enabled() -> bool:
    return recipient() is not None


def _one_line(text: str) -> str:
    """Without line breaks or other control characters: nothing a visitor
    types may start a new line in an email header."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", text or "").split())


def _over_limit(ip: str | None, now: datetime) -> bool:
    day_ago = now - timedelta(days=1)
    while _sent and _sent[0][0] < day_ago:
        _sent.popleft()
    if len(_sent) >= PER_DAY:
        logger.warning("Contact form: the daily limit of %s messages is reached", PER_DAY)
        return True
    if ip is None:
        return False
    return any(
        sum(1 for at, sender in _sent if sender == ip and at >= now - window) >= limit
        for window, limit in PER_IP
    )


def email_for(name: str, email: str, message: str, language: str, account: dict | None,
              now: datetime) -> tuple[str, str, str]:
    """(subject, plain text, HTML) of the email to the team. In English,
    whatever the visitor's language; the message itself is as they wrote it."""
    if account:
        signed_in = f"signed in as {account['email']} (user {account['id']})"
    else:
        signed_in = "not signed in"
    subject = f"{SUBJECT_PREFIX} {name}"
    head = "\n".join([
        "A message from the contact form.",
        "",
        f"From:     {name} <{email}>",
        f"Account:  {signed_in}",
        f"Language: {language}",
        f"Sent:     {now.strftime('%Y-%m-%d %H:%M')} UTC",
    ])
    foot = f"Answering this email answers {name} at {email} (Reply-To)."
    rule = "-" * 60
    plain = f"{head}\n\n{rule}\n{message}\n{rule}\n\n{foot}\n"
    html = (
        '<!DOCTYPE html><html lang="en"><body style="font-family: sans-serif; line-height: 1.5">'
        f"<pre>{mailer.escape(head)}</pre>"
        '<blockquote style="margin: 16px 0; padding: 8px 14px; border-left: 4px solid #0f6e5f;'
        f' white-space: pre-wrap">{mailer.escape(message)}</blockquote>'
        f'<p style="font-size: 13px; color: #555">{mailer.escape(foot)}</p>'
        "</body></html>"
    )
    return subject, plain, html


def send(name: str, email: str, message: str, language: str = "en", website: str = "",
         ip: str | None = None, account: dict | None = None) -> None:
    """Email a visitor's message to the team. Raises the API's errors:
    contact_unavailable (switched off), contact_incomplete, invalid_email,
    too_many_requests, email_unavailable (it couldn't be sent)."""
    to = recipient()
    if to is None:
        raise api_error(503, "contact_unavailable", "The contact form isn't available yet")
    name = _one_line(name)[:MAX_NAME_CHARS]
    message = (message or "").strip()[:MAX_MESSAGE_CHARS]
    if not name or not message or not (email or "").strip():
        raise api_error(400, "contact_incomplete", "A name, an email address and a message are needed")
    email = auth.normalize_email(email)
    language = language if language in mailer.LANGUAGES else "en"
    if (website or "").strip():
        logger.info("Contact form: a message with the hidden field filled in was dropped")
        return

    now = datetime.now(timezone.utc)
    with _lock:
        if _over_limit(ip, now):
            raise api_error(429, "too_many_requests", "Too many messages from here; try again later")
        # Counted before it's sent, so that messages sent at the same moment
        # can't pass the limit together; taken back if it couldn't be sent.
        entry = (now, ip)
        _sent.append(entry)
    subject, plain, html = email_for(name, email, message, language, account, now)
    try:
        mailer.backend().send(to, subject, plain, html, reply_to=email)
    except mailer.EmailError:
        with _lock:
            if entry in _sent:
                _sent.remove(entry)
        logger.exception("Sending a contact message failed")
        raise api_error(503, "email_unavailable", "The message couldn't be sent; try again later")
    # Neither who wrote nor what: the log is not where messages are kept.
    logger.info("Contact form: a message of %s characters was sent to the team", len(message))
