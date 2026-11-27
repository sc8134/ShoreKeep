from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import settings

logger = logging.getLogger(__name__)


def _send_smtp(to: str, subject: str, body_text: str, body_html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        if settings.smtp_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls(context=context)
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, to, msg.as_string())
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, to, msg.as_string())
    except Exception as exc:
        logger.error("failed to send email to %s: %s", to, exc)
        raise


def send_email(to: str, subject: str, body_text: str, body_html: str) -> None:
    """Send an email. Falls back to stdout logging when SMTP is not configured."""
    if not settings.smtp_host:
        logger.info(
            "EMAIL (dev, no SMTP configured)\nTo: %s\nSubject: %s\n---\n%s",
            to,
            subject,
            body_text,
        )
        return
    _send_smtp(to, subject, body_text, body_html)


def send_verification_email(to: str, token: str) -> None:
    base = settings.public_base_url.rstrip("/")
    link = f"{base}/v1/auth/verify?token={token}"
    subject = "Verify your Shorekeep email address"
    text = (
        f"Welcome to Shorekeep!\n\n"
        f"Click the link below to verify your email address. "
        f"The link expires in 24 hours.\n\n"
        f"{link}\n\n"
        f"If you did not create a Shorekeep account, you can ignore this email."
    )
    html = (
        f"<p>Welcome to Shorekeep!</p>"
        f"<p>Click the link below to verify your email address. "
        f"The link expires in 24 hours.</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>If you did not create a Shorekeep account, you can ignore this email.</p>"
    )
    send_email(to, subject, text, html)


def send_password_reset_email(to: str, token: str) -> None:
    base = settings.public_base_url.rstrip("/")
    link = f"{base}/v1/auth/reset-password?token={token}"
    subject = "Reset your Shorekeep password"
    text = (
        f"You requested a password reset for your Shorekeep account.\n\n"
        f"Click the link below to set a new password. "
        f"The link expires in 1 hour.\n\n"
        f"{link}\n\n"
        f"If you did not request a password reset, you can ignore this email."
    )
    html = (
        f"<p>You requested a password reset for your Shorekeep account.</p>"
        f"<p>Click the link below to set a new password. "
        f"The link expires in 1 hour.</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>If you did not request a password reset, you can ignore this email.</p>"
    )
    send_email(to, subject, text, html)
