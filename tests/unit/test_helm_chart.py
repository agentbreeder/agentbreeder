"""Helm chart renders cleanly (P5). Skips render if helm is unavailable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "agentbreeder"


def _chart_without_deps(tmp_path: Path) -> Path:
    """Copy the chart, stripping the ``dependencies:`` block.

    helm checks dependency *presence* at load time even for condition-disabled
    deps, so ``helm template`` would otherwise require the Bitnami charts to be
    fetched (network). Stripping the block lets us validate *our* templates
    offline and deterministically.
    """
    dst = tmp_path / "agentbreeder"
    shutil.copytree(CHART, dst)
    chart_yaml = dst / "Chart.yaml"
    lines = chart_yaml.read_text().splitlines()
    kept: list[str] = []
    for line in lines:
        if line.startswith("dependencies:"):
            break
        kept.append(line)
    chart_yaml.write_text("\n".join(kept) + "\n")
    return dst


@pytest.fixture(scope="module")
def helm():
    exe = shutil.which("helm")
    if not exe:
        pytest.skip("helm not installed")
    return exe


def test_chart_lint_structure():
    # Chart.yaml + key templates exist regardless of helm availability.
    assert (CHART / "Chart.yaml").exists()
    assert (CHART / "values.yaml").exists()
    for t in [
        "api-deployment.yaml",
        "dashboard-deployment.yaml",
        "ingress.yaml",
        "secret.yaml",
        "configmap.yaml",
        "migrate-job.yaml",
        "_helpers.tpl",
    ]:
        assert (CHART / "templates" / t).exists(), t


def _render(helm: str, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    chart = _chart_without_deps(tmp_path)
    return subprocess.run(
        [helm, "template", "ab", str(chart), *extra],
        capture_output=True,
        text=True,
    )


def test_helm_template_renders_external(helm, tmp_path):
    # External DB/Redis path: no bundled passwords required.
    out = _render(
        helm,
        tmp_path,
        "--set",
        "postgresql.enabled=false",
        "--set",
        "redis.enabled=false",
        "--set",
        "externalDatabaseUrl=postgresql+asyncpg://u:p@db:5432/d",
        "--set",
        "externalRedisUrl=redis://r:6379",
        "--set",
        "host=example.com",
    )
    assert out.returncode == 0, out.stderr
    assert "kind: Deployment" in out.stdout
    assert "kind: Ingress" in out.stdout
    assert "DATABASE_URL" in out.stdout
    assert "rajits/agentbreeder-api:2.6.0" in out.stdout
    # No shipped/default secrets or mutable tags.
    assert "change-me" not in out.stdout
    assert ":latest" not in out.stdout


def test_helm_bundled_requires_db_password(helm, tmp_path):
    # Bundled Postgres with no password must abort the render.
    out = _render(helm, tmp_path, "--set", "host=x.com")
    assert out.returncode != 0
    assert "postgresql.auth.password must be set" in out.stderr


def test_helm_tls_restricts_cors_to_https(helm, tmp_path):
    out = _render(
        helm,
        tmp_path,
        "--set",
        "postgresql.auth.password=pw",
        "--set",
        "redis.auth.password=pw",
        "--set",
        "ingress.tls.enabled=true",
        "--set",
        "host=secure.example.com",
    )
    assert out.returncode == 0, out.stderr
    assert 'CORS_ORIGINS: "[\\"https://secure.example.com\\"]"' in out.stdout
    assert "force-ssl-redirect" in out.stdout
    assert "redis://:pw@" in out.stdout
