"""Unit tests for GCP Cloud SQL private-IP selection (P2).

The SDK call is mocked out elsewhere; this covers the pure selection logic
that turns a Cloud SQL instance's ``ip_addresses`` into a connectable host.
"""

from __future__ import annotations

from types import SimpleNamespace

from engine.provisioners.gcp import _select_private_ip


def _ip(addr: str, type_name: str) -> SimpleNamespace:
    # Mimic a proto-plus enum whose .name is the type, plus .ip_address.
    return SimpleNamespace(ip_address=addr, type_=SimpleNamespace(name=type_name))


def test_selects_private_over_primary():
    ips = [_ip("203.0.113.9", "PRIMARY"), _ip("10.1.2.3", "PRIVATE")]
    assert _select_private_ip(ips) == "10.1.2.3"


def test_returns_none_when_no_private():
    ips = [_ip("203.0.113.9", "PRIMARY"), _ip("198.51.100.4", "OUTGOING")]
    assert _select_private_ip(ips) is None


def test_handles_plain_type_attribute_and_qualified_name():
    # Object exposing `.type` (not `.type_`) and a stringified enum value.
    plain = SimpleNamespace(ip_address="10.9.9.9", type="SqlIpAddressType.PRIVATE")
    assert _select_private_ip([plain]) == "10.9.9.9"


def test_empty_and_none():
    assert _select_private_ip([]) is None
    assert _select_private_ip(None) is None
