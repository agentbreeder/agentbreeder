"""The dashboard nginx template renders API_UPSTREAM from env (P5)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "dashboard" / "nginx.conf.template"
ENTRYPOINT = REPO / "dashboard" / "docker-entrypoint.sh"


def _render(env: dict[str, str]) -> str:
    # Mirror the container entrypoint: envsubst with an explicit var allow-list.
    envsubst = shutil.which("envsubst")
    if not envsubst:
        pytest.skip("envsubst not installed")
    full = {**os.environ, **env}
    out = subprocess.run(
        [envsubst, "${API_UPSTREAM} ${LISTEN_PORT}"],
        input=TEMPLATE.read_text(),
        capture_output=True,
        text=True,
        env=full,
        check=True,
    )
    return out.stdout


def test_template_has_placeholders():
    text = TEMPLATE.read_text()
    assert "${API_UPSTREAM}" in text
    assert "${LISTEN_PORT}" in text
    # nginx runtime vars must NOT be in the substitution list (they stay literal).
    assert "$host" in text


def test_entrypoint_uses_explicit_var_list():
    text = ENTRYPOINT.read_text()
    # Only our two vars are substituted, so nginx's own $host/$uri survive.
    assert "envsubst '${API_UPSTREAM} ${LISTEN_PORT}'" in text
    assert 'exec "$@"' in text


def test_renders_custom_upstream():
    rendered = _render({"API_UPSTREAM": "http://my-api:9000", "LISTEN_PORT": "3001"})
    assert "proxy_pass http://my-api:9000" in rendered
    assert "listen 3001;" in rendered
    assert "$host" in rendered  # nginx runtime var preserved
    assert "${" not in rendered  # all our placeholders substituted
