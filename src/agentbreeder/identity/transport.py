"""Bounded JSON-over-HTTPS client: timeouts, size limits, no redirects, no proxies by default."""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 10.0


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        form: dict[str, str] | None = None,
    ) -> tuple[int, Any]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str, *, allow_loopback_http: bool = False) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise TransportError("PROVIDER_ERROR", "Invalid provider URL")
    if parsed.scheme == "https":
        return url
    if parsed.scheme == "http" and allow_loopback_http and _is_loopback(parsed.hostname):
        return url
    raise TransportError("PROVIDER_ERROR", "Provider URLs must use HTTPS (or loopback HTTP)")


class JsonTransport:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT, allow_loopback_http: bool = False):
        self.timeout = timeout
        self.allow_loopback_http = allow_loopback_http
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def request(self, method, url, *, headers=None, json_body=None, form=None):
        check_url(url, allow_loopback_http=self.allow_loopback_http)
        data = None
        all_headers = {"Accept": "application/json", **(headers or {})}
        if json_body is not None:
            data = json.dumps(json_body).encode()
            all_headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, self._body(response)
        except urllib.error.HTTPError as error:
            try:
                return error.code, self._body(error)
            except TransportError:
                return error.code, None
        except TimeoutError as error:
            raise TransportError("PROVIDER_TIMEOUT", "Identity provider timed out") from error
        except (urllib.error.URLError, OSError) as error:
            if isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout)):
                raise TransportError("PROVIDER_TIMEOUT", "Identity provider timed out") from error
            raise TransportError("PROVIDER_ERROR", "Identity provider unreachable") from error

    @staticmethod
    def _body(response) -> Any:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError("PROVIDER_ERROR", "Identity provider response too large")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise TransportError(
                "PROVIDER_ERROR", "Identity provider returned invalid JSON"
            ) from (error)
