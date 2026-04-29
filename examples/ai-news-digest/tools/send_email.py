"""Local override: send the digest email via Gmail SMTP."""
from __future__ import annotations

from typing import Any

from tools.impl import send_email as _impl

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Email subject line."},
        "body": {"type": "string", "description": "Email body (plain text or markdown)."},
    },
    "required": ["subject", "body"],
}


def send_email(subject: str, body: str) -> dict:
    """Send digest to RECIPIENT_EMAILS via Gmail SMTP.

    Args:
        subject: Subject line.
        body: Email body.

    Returns:
        {sent: bool, recipients: list[str], message_id: str}.
    """
    return _impl(subject=subject, body=body)
