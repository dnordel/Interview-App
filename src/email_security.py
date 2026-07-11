from __future__ import annotations

import re


_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def sanitize_email_subject(subject: str) -> str:
    value = str(subject or "")
    return re.sub(r"[\r\n]+", " ", value).strip()


def is_valid_email_address(value: str) -> bool:
    return bool(_EMAIL_PATTERN.match(str(value or "").strip()))


def sender_email_error_reason(value: str) -> str | None:
    email = str(value or "").strip()
    if not email:
        return "Sender email is required."
    if not is_valid_email_address(email):
        return "Sender email must be a valid email address."
    return None


def sender_email_domain_type(value: str) -> str:
    email = str(value or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain == "launchpadpreschool.com" or domain.endswith(".launchpadpreschool.com"):
        return "company"
    if domain in {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}:
        return "personal"
    return "external"

__all__ = [
    "is_valid_email_address",
    "sanitize_email_subject",
    "sender_email_domain_type",
    "sender_email_error_reason",
]
