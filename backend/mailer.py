"""
Sending email. The only email today is the sign-in link (auth.py).

The backend is chosen by the EMAIL_BACKEND environment variable:
  - "console" (the default, for development and tests): nothing is sent;
    the email, link included, is written to the log and kept in `outbox`.
  - "ses": Amazon SES (API v2) in SES_REGION (default eu-north-1), from
    EMAIL_FROM (an address, e.g. no-reply@rideandrail.ch, shown as "Ride and
    Rail <no-reply@rideandrail.ch>"). Credentials come from the server's
    instance role (infra/email.tf), which may only send from that address.

Texts are translated like the rest of the UI, in
frontend/i18n/<lang>/email.json ("email.*" keys, see translations.py), so
backend/tests/test_i18n.py checks them too (all four languages, same
placeholders, formal address).
"""
import logging
import os
from functools import cache
from html import escape  # noqa: F401 (also for reports.py)

import translations

logger = logging.getLogger("uvicorn.error")

LANGUAGES = ("de", "fr", "it", "en")
DEFAULT_SES_REGION = "eu-north-1"
SENDER_NAME = "Ride and Rail"


class EmailError(Exception):
    """The email couldn't be sent (misconfiguration or the provider failed)."""


class ConsoleBackend:
    """Development and tests: logs emails instead of sending them."""

    def __init__(self):
        self.outbox: list[dict] = []

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html})
        logger.info("Email (not sent, EMAIL_BACKEND=console) to %s: %s\n%s", to, subject, text)


class SesBackend:
    """Amazon SES, API v2."""

    def __init__(self, sender: str, region: str):
        import boto3  # only needed in production

        self.sender = sender
        self.client = boto3.client("sesv2", region_name=region)

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.send_email(
                FromEmailAddress=self.sender,
                Destination={"ToAddresses": [to]},
                Content={"Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text, "Charset": "UTF-8"},
                        "Html": {"Data": html, "Charset": "UTF-8"},
                    },
                }},
            )
        except (BotoCoreError, ClientError) as e:
            raise EmailError(str(e)) from e


_backend = None


def backend():
    """The configured backend (created on first use)."""
    global _backend
    if _backend is None:
        kind = os.environ.get("EMAIL_BACKEND") or "console"
        if kind == "console":
            _backend = ConsoleBackend()
        elif kind == "ses":
            sender = os.environ.get("EMAIL_FROM", "").strip()
            if not sender:
                raise EmailError("EMAIL_BACKEND=ses needs EMAIL_FROM")
            if "<" not in sender:
                sender = f"{SENDER_NAME} <{sender}>"
            _backend = SesBackend(sender, os.environ.get("SES_REGION") or DEFAULT_SES_REGION)
        else:
            raise EmailError(f"Unknown EMAIL_BACKEND: {kind!r}")
    return _backend


@cache
def _dictionary(language: str) -> dict:
    return translations.dictionary(language)


def text(language: str, key: str, **params) -> str:
    """A translated string, English if the language or key is missing."""
    language = language if language in LANGUAGES else "en"
    template = _dictionary(language).get(key) or _dictionary("en")[key]
    for name, value in params.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def sign_in_email(language: str, link: str, minutes: int, code: str) -> tuple[str, str, str]:
    """(subject, plain text, HTML) of the sign-in email: the link, and the
    code to type instead where the link can't be opened (auth.verify_code)."""
    t = lambda key, **p: text(language, f"email.signIn.{key}", **p)  # noqa: E731
    subject = t("subject")
    paragraphs = [t("greeting"), t("intro", minutes=minutes)]
    after = [t("code", code=code), t("ignore"), t("signature")]
    plain = "\n\n".join([*paragraphs, link, *after]) + "\n"
    html = (
        f'<!DOCTYPE html><html lang="{escape(language)}"><body style="font-family: sans-serif; line-height: 1.5">'
        + "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
        + f'<p><a href="{escape(link)}" style="display: inline-block; padding: 10px 18px; background: #0f6e5f;'
        f' color: #ffffff; text-decoration: none; border-radius: 6px">{escape(t("button"))}</a></p>'
        + f'<p style="font-size: 13px; color: #555">{escape(t("linkHint"))}<br>{escape(link)}</p>'
        + "".join(f"<p>{escape(p)}</p>" for p in after)
        + "</body></html>"
    )
    return subject, plain, html


def send_sign_in_link(to: str, language: str, link: str, minutes: int, code: str) -> None:
    """Send a sign-in link and its code. Raises EmailError if it can't be sent."""
    subject, plain, html = sign_in_email(language, link, minutes, code)
    backend().send(to, subject, plain, html)
