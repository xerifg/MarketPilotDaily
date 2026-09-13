"""163 SMTP transport. A durable delivery claim is required before sending."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from hashlib import sha256
from html import escape
import re
import smtplib
import ssl
from typing import Protocol
from urllib.parse import urlsplit


class MailError(Exception):
    """Only safe status codes; never include SMTP responses or credentials."""


class DeliveryGateway(Protocol):
    """D1 must atomically claim a report once and persist 'sending' before SMTP.

    Existing claims (including failed/uncertain ones) must return False. A new
    attempt requires explicit reconciliation; a runner retry cannot reset it.
    """

    def claim(self, report_id: str) -> bool: ...
    def finish(self, report_id: str, state: str, error_code: str | None) -> None: ...


@dataclass(frozen=True)
class MailConfig:
    username: str = field(repr=False)
    auth_code: str = field(repr=False)
    sender: str = field(repr=False)
    recipient: str = field(repr=False)

    def validate(self):
        # This deployment is confirmed to send from/to the same 163 account.
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+@163\.com", self.username)
                or self.sender != self.username or self.recipient != self.username):
            raise MailError("invalid_mail_account")
        if not self.auth_code or any(char.isspace() for char in self.auth_code):
            raise MailError("missing_or_invalid_smtp_auth_code")


def build_message(config: MailConfig, report_id: str, subject: str,
                  paragraphs: list[str], sources: list[tuple[str, str]]) -> EmailMessage:
    """Accept the report's email summary only, never the raw portfolio snapshot.

    All text is escaped. Source links are separately validated; model HTML is
    never rendered. The report builder remains responsible for data minimization.
    """
    config.validate()
    if not isinstance(report_id, str) or not 1 <= len(report_id) <= 200:
        raise MailError("invalid_report_id")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 200 or any(ord(c) < 32 for c in subject):
        raise MailError("invalid_subject")
    if not paragraphs or len(paragraphs) > 100 or any(not isinstance(p, str) for p in paragraphs):
        raise MailError("invalid_mail_summary")
    if len(sources) > 40:
        raise MailError("too_many_sources")
    for label, url in sources:
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and not any(c.isspace() or ord(c) < 32 for c in url))
        except ValueError:
            valid = False
        if not isinstance(label, str) or not valid:
            raise MailError("invalid_source_link")
    message = EmailMessage(policy=SMTP)
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = f"<marketpilot.{sha256(report_id.encode()).hexdigest()}@163.com>"
    text = "\n\n".join(paragraphs)
    if sources:
        text += "\n\n信息来源：\n" + "\n".join(f"{label}: {url}" for label, url in sources)
    markup = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>'
    markup += "".join(f'<p>{escape(p).replace(chr(10), "<br>")}</p>' for p in paragraphs)
    if sources:
        markup += "<h2>信息来源</h2><ul>" + "".join(
            f'<li><a href="{escape(url, quote=True)}">{escape(label)}</a></li>' for label, url in sources) + "</ul>"
    message.set_content(text, cte="base64")
    message.add_alternative(markup + "</body></html>", subtype="html", cte="base64")
    if len(message.as_bytes()) > 200_000:
        raise MailError("mail_too_large")
    return message


def send_report(config: MailConfig, gateway: DeliveryGateway, report_id: str,
                subject: str, paragraphs: list[str], sources: list[tuple[str, str]]) -> str:
    message = build_message(config, report_id, subject, paragraphs, sources)
    payload = message.as_bytes()
    try:
        claimed = gateway.claim(report_id)
    except Exception:
        raise MailError("delivery_claim_unavailable") from None
    if not claimed:
        raise MailError("delivery_already_claimed_or_paused")
    client = None
    data_started = False
    state, error_code = "failed_before_data", "smtp_connection_failed"
    try:
        client = smtplib.SMTP_SSL("smtp.163.com", 465, timeout=30, context=ssl.create_default_context())
        error_code = "smtp_greeting_failed"
        code, _ = client.ehlo()
        if code != 250:
            raise smtplib.SMTPHeloError(code, b"")
        error_code = "smtp_auth_failed"
        client.login(config.username, config.auth_code)
        error_code = "smtp_sender_rejected"
        code, _ = client.mail(config.sender)
        if code != 250:
            raise smtplib.SMTPSenderRefused(code, b"", "")
        error_code = "smtp_recipient_rejected"
        code, _ = client.rcpt(config.recipient)
        if code not in (250, 251):
            raise smtplib.SMTPRecipientsRefused({})
        data_started = True
        state, error_code = "delivery_uncertain", "smtp_data_result_unknown"
        code, _ = client.data(payload)
        if code != 250:
            state, error_code = "smtp_rejected", "smtp_data_rejected"
        else:
            state, error_code = "smtp_accepted", None
    except smtplib.SMTPDataError:
        state, error_code = "smtp_rejected", "smtp_data_rejected"
    except Exception:
        # A disconnect/timeout after DATA may mean accepted mail. Never retry.
        if data_started:
            state, error_code = "delivery_uncertain", "smtp_data_result_unknown"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass  # A close error cannot undo the server's DATA acceptance.
    try:
        gateway.finish(report_id, state, error_code)
    except Exception:
        # Persistent 'sending' still blocks duplicate sends after a lost write.
        raise MailError("delivery_result_unrecorded") from None
    return state
