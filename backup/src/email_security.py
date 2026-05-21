from __future__ import annotations

import re


_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
}


def sanitize_email_subject(subject: str) -> str:
    text = str(subject or "")
    return text.replace("\r", " ").replace("\n", " ").strip()


def is_valid_email_address(value: str) -> bool:
    return bool(_EMAIL_PATTERN.match(str(value or "").strip()))


def sender_email_error_reason(value: str) -> str | None:
    email = str(value or "").strip()
    if not email:
        return "missing"
    if not is_valid_email_address(email):
        return "invalid_format"
    return None


def sender_email_domain_type(value: str) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email:
        return "unknown"
    domain = email.split("@", 1)[1]
    if domain in _PUBLIC_EMAIL_DOMAINS:
        return "public"
    if domain.endswith(".edu"):
        return "education"
    return "organization"
