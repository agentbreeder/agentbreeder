"""Tests for the SMTP email connector."""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from connectors.email.smtp import SMTPConnector, SMTPConnectorError


class TestSMTPConnectorInit:
    def test_defaults_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_USE_TLS", "false")
        c = SMTPConnector()
        assert c._host == "mail.example.com"
        assert c._port == 465
        assert c._user == "user@example.com"
        assert c._password == "secret"
        assert c._use_tls is False

    def test_constructor_args_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMTP_HOST", "env-host.com")
        c = SMTPConnector(host="arg-host.com", port=25, user="u", password="p", use_tls=False)
        assert c._host == "arg-host.com"
        assert c._port == 25
        assert c._use_tls is False

    def test_use_tls_defaults_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SMTP_USE_TLS", raising=False)
        c = SMTPConnector()
        assert c._use_tls is True

    def test_name(self) -> None:
        assert SMTPConnector().name == "smtp"


class TestSMTPConnectorIsAvailable:
    @pytest.mark.asyncio
    async def test_available_when_connection_succeeds(self) -> None:
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_smtp):
            c = SMTPConnector(host="localhost", port=587)
            result = await c.is_available()
        assert result is True

    @pytest.mark.asyncio
    async def test_unavailable_on_connection_error(self) -> None:
        with patch("smtplib.SMTP", side_effect=OSError("refused")):
            c = SMTPConnector(host="bad-host", port=999)
            result = await c.is_available()
        assert result is False


class TestSMTPConnectorScan:
    @pytest.mark.asyncio
    async def test_scan_returns_metadata(self) -> None:
        c = SMTPConnector(host="smtp.example.com", port=587, user="u@e.com")
        result = await c.scan()
        assert len(result) == 1
        entry = result[0]
        assert entry["source"] == "smtp"
        assert "smtp.example.com" in entry["description"]
        assert "send_email" in entry["capabilities"]
        assert entry["config"]["authenticated"] is True


class TestSMTPConnectorSend:
    def test_send_plain_text(self) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_conn):
            c = SMTPConnector(
                host="smtp.example.com", port=587, user="u@e.com", password="pw", use_tls=True
            )
            c.send(to=["dest@example.com"], subject="Hi", body="Hello!")
        mock_conn.starttls.assert_called_once()
        mock_conn.login.assert_called_once_with("u@e.com", "pw")
        mock_conn.sendmail.assert_called_once()

    def test_send_html(self) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_conn):
            c = SMTPConnector(host="smtp.example.com", port=587, user="u@e.com", use_tls=False)
            c.send(to=["a@b.com"], subject="Sub", body="text", html="<b>html</b>")
        mock_conn.sendmail.assert_called_once()
        # No starttls when use_tls=False
        mock_conn.starttls.assert_not_called()

    def test_send_raises_without_sender(self) -> None:
        c = SMTPConnector(host="smtp.example.com", port=587)
        with pytest.raises(SMTPConnectorError, match="No sender"):
            c.send(to=["a@b.com"], subject="S", body="B")

    def test_send_raises_without_host(self) -> None:
        c = SMTPConnector(user="u@e.com")
        with pytest.raises(SMTPConnectorError, match="No SMTP host"):
            c.send(to=["a@b.com"], subject="S", body="B")

    def test_send_wraps_smtp_exception(self) -> None:
        with patch("smtplib.SMTP", side_effect=smtplib.SMTPException("auth failed")):
            c = SMTPConnector(host="smtp.example.com", port=587, user="u", password="p")
            with pytest.raises(SMTPConnectorError, match="auth failed"):
                c.send(to=["a@b.com"], subject="S", body="B")

    def test_send_wraps_os_error(self) -> None:
        with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            c = SMTPConnector(host="bad.host", port=9, user="u")
            with pytest.raises(SMTPConnectorError, match="connection refused"):
                c.send(to=["a@b.com"], subject="S", body="B")

    def test_send_uses_from_addr_override(self) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("smtplib.SMTP", return_value=mock_conn):
            c = SMTPConnector(host="smtp.example.com", port=587, use_tls=False)
            c.send(to=["a@b.com"], subject="S", body="B", from_addr="custom@example.com")
        _, call_args, _ = mock_conn.sendmail.mock_calls[0]
        assert call_args[0] == "custom@example.com"


class TestSMTPConnectorSendAsync:
    @pytest.mark.asyncio
    async def test_send_async_delegates_to_send(self) -> None:
        c = SMTPConnector(host="smtp.example.com", port=587, user="u@e.com", use_tls=False)
        with patch.object(c, "send") as mock_send:
            await c.send_async(to=["a@b.com"], subject="S", body="B")
        mock_send.assert_called_once_with(["a@b.com"], "S", "B", None, None)
