"""Shared adapter helpers: safe HTTP status handling and strict fact parsing."""

from __future__ import annotations

import re
import urllib.parse
from datetime import date, datetime
from typing import Any

from agentbreeder.identity import ProviderError
from agentbreeder.identity.transport import Transport, TransportError

MAX_PAGES = 20
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{0,255}$")


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ProviderError("PROVIDER_ERROR", "Unsupported principal reference format")
    return urllib.parse.quote(value, safe="")


def call(transport: Transport, method: str, url: str, **kwargs) -> Any:
    try:
        status, body = transport.request(method, url, **kwargs)
    except TransportError as error:
        raise ProviderError(error.code, str(error)) from error
    if status == 404:
        raise ProviderError("PRINCIPAL_NOT_FOUND", "Principal not found in identity provider")
    if status in (401, 403):
        raise ProviderError("PROVIDER_ERROR", "Identity provider denied the read request")
    if not 200 <= status < 300:
        raise ProviderError("PROVIDER_ERROR", f"Identity provider returned HTTP {status}")
    return body


def paged(transport: Transport, url: str, headers: dict, next_key: str = "@odata.nextLink"):
    """Follow server paging links on the same origin, bounded."""
    origin = urllib.parse.urlsplit(url)[:2]
    for _ in range(MAX_PAGES):
        body = call(transport, "GET", url, headers=headers)
        if not isinstance(body, dict) or not isinstance(body.get("value"), list):
            raise ProviderError("PROVIDER_ERROR", "Unexpected identity provider response shape")
        yield from body["value"]
        url = body.get(next_key)
        if not url:
            return
        if urllib.parse.urlsplit(url)[:2] != origin:
            raise ProviderError("PROVIDER_ERROR", "Refusing cross-origin paging link")
    raise ProviderError("PROVIDER_ERROR", "Identity provider result exceeds paging limit")


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    raise ProviderError("PROVIDER_ERROR", "Unparseable review date; failing closed")


def require_setting(settings, key: str) -> Any:
    value = settings.get(key)
    if value in (None, ""):
        raise ProviderError("PROVIDER_ERROR", f"Provider setting {key} is required")
    return value
