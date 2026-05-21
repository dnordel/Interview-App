from __future__ import annotations

import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

from email_security import sanitize_email_subject


class DirectorEmailDraftError(RuntimeError):
    pass


def open_outlook_draft(*, subject: str, body: str, attachments: list[str], to_recipients: str = "") -> None:
    if not sys.platform.startswith("win"):
        raise DirectorEmailDraftError("Outlook draft is only supported on Windows.")

    existing_files = [str(Path(path).expanduser()) for path in attachments if Path(path).expanduser().exists()]
    escaped_subject = _ps_quote(sanitize_email_subject(subject))
    escaped_body = _ps_quote(body)
    escaped_to = _ps_quote(to_recipients)
    attachment_script = "\n".join(
        [f"$mail.Attachments.Add('{_ps_quote(path)}') | Out-Null" for path in existing_files]
    )

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $outlook = New-Object -ComObject Outlook.Application\n"
        "  if ($null -eq $outlook) { throw 'Outlook COM automation is unavailable.' }\n"
        "  $mail = $outlook.CreateItem(0)\n"
        "  if ($null -eq $mail) { throw 'Unable to create an Outlook draft item.' }\n"
        f"  $mail.Subject = '{escaped_subject}'\n"
        f"  $mail.Body = '{escaped_body}'\n"
        f"  $mail.To = '{escaped_to}'\n"
        f"  {attachment_script}\n"
        "  $mail.Display()\n"
        "} catch {\n"
        "  Write-Error $_.Exception.Message\n"
        "  exit 1\n"
        "}\n"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise DirectorEmailDraftError(f"Could not open Outlook draft. {stderr}".strip()) from exc


def build_mailto_url(*, subject: str, body: str, to_recipients: str = "") -> str:
    recipient_value = _normalize_mailto_recipients(to_recipients)
    query_parts: list[str] = []
    subject_value = sanitize_email_subject(subject)
    body_value = str(body or "").strip()
    if subject_value:
        query_parts.append(f"subject={quote(subject_value)}")
    if body_value:
        query_parts.append(f"body={quote(body_value)}")
    query = "&".join(query_parts)
    if not query:
        return f"mailto:{recipient_value}"
    return f"mailto:{recipient_value}?{query}"


def _normalize_mailto_recipients(to_recipients: str) -> str:
    raw_recipients = str(to_recipients or "").strip()
    if not raw_recipients:
        return ""

    uses_semicolon_separator = ";" in raw_recipients
    separator = ";" if uses_semicolon_separator else ","
    recipients = [token.strip() for token in raw_recipients.replace(";", ",").split(",")]
    encoded_recipients = [quote(recipient, safe="@") for recipient in recipients if recipient]
    return separator.join(encoded_recipients)


def _ps_quote(value: str) -> str:
    return str(value or "").replace("'", "''")
