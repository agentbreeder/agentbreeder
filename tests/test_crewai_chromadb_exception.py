"""The `chromadb` security exception for `runtime/crewai@1.0.0` (#1090), kept narrow.

`crewai` depends on `chromadb` 1.5.9, which has four advisories in ChromaDB's HTTP server and no
fixed version. The owner accepted them (CLAUDE.md, "Known security exceptions") because the
runtime imports `chromadb` only as a library and never starts its server. These tests keep that
true and keep the exception's scope: exactly four IDs, one package version, one lock. The
runtime's own test (`test_chromadb_is_a_library_only_no_server_client_or_port`, CI job
`runtime-crewai`) checks the same against real CrewAI and chromadb.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentbreeder import registry

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(registry.BUILTIN_ROOT) / "runtime/crewai/1.0.0"
LOCK = RUNTIME / "runtime/requirements.lock"
IDS = {
    "GHSA-f4j7-r4q5-qw2c",
    "GHSA-36p7-vc44-83pf",
    "GHSA-xph7-9rjv-w5fr",
    "GHSA-2wm9-hf6c-p5cr",
}
# The same four as pip-audit names them (PYSEC) and their CVEs.
ALIASES = {
    "PYSEC-2026-311",
    "PYSEC-2026-3814",
    "PYSEC-2026-3815",
    "PYSEC-2026-3813",
    "CVE-2026-45829",
    "CVE-2026-45833",
    "CVE-2026-45831",
    "CVE-2026-45830",
}
# What would start a ChromaDB server, or a client that builds its server-side system.
CHROMADB_SERVER_OR_CLIENT = re.compile(
    r"chromadb\.(server|app|cli)\b"
    r"|chromadb_rust_bindings"
    r"|\b(Http|AsyncHttp|Persistent|Ephemeral|Cloud|Rust|Admin)Client\b"
    r"|chromadb\.Client\b"
    r"|SharedSystemClient"
    r"|\bchroma\s+run\b"
    r"|uvicorn"
)


def _exception_section() -> str:
    text = (ROOT / "CLAUDE.md").read_text()
    start = text.index("## 🔐 Known security exceptions")
    end = text.index("\n---", start)
    return text[start:end]


def test_the_exception_names_exactly_the_four_ids_and_only_the_crewai_lock():
    section = _exception_section()
    assert set(re.findall(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}", section)) == IDS
    assert set(re.findall(r"(?:CVE|PYSEC)-\d{4}-\d+", section)) == ALIASES
    locks = set(re.findall(r"[\w/.@-]+requirements\.lock", section))
    assert locks == {LOCK.relative_to(ROOT).as_posix()}
    assert "chromadb==1.5.9" in section


def test_the_lock_still_pins_the_excepted_chromadb_version():
    """A new chromadb pin means re-checking the advisories and updating or removing the
    exception in CLAUDE.md; this fails until that is done."""
    pins = re.findall(r"^chromadb==(\S+)", LOCK.read_text(), flags=re.MULTILINE)
    assert pins == ["1.5.9"], (
        "chromadb changed in runtime/crewai's lock: re-check the advisories and update the "
        "chromadb exception in CLAUDE.md"
    )


def test_no_other_builtin_lock_carries_chromadb():
    """The exception covers one lock; chromadb anywhere else is not excepted."""
    carriers = sorted(
        path.relative_to(registry.BUILTIN_ROOT).as_posix()
        for path in Path(registry.BUILTIN_ROOT).rglob("requirements*.lock")
        if re.search(r"^chromadb==", path.read_text(), flags=re.MULTILINE)
    )
    assert carriers == ["runtime/crewai/1.0.0/runtime/requirements.lock"]


def test_the_image_opens_only_the_protocol_port_and_runs_only_the_runtime():
    dockerfile = (RUNTIME / "Dockerfile").read_text()
    lines = [line.strip() for line in dockerfile.splitlines()]
    assert [line for line in lines if line.startswith("EXPOSE")] == ["EXPOSE 8080"]
    assert [line for line in lines if line.startswith(("CMD", "ENTRYPOINT"))] == [
        'CMD ["python", "/app/runtime/server.py"]'
    ]
    assert "chroma" not in dockerfile.lower()


def test_no_runtime_source_starts_a_chromadb_server_or_client():
    sources = sorted((RUNTIME / "runtime").rglob("*.py"))
    assert len(sources) > 5
    found = {
        path.relative_to(RUNTIME).as_posix(): match.group(0)
        for path in sources
        for match in [CHROMADB_SERVER_OR_CLIENT.search(path.read_text())]
        if match
    }
    assert found == {}


def test_the_pattern_catches_what_it_is_for():
    for line in (
        "import chromadb.server.fastapi",
        "from chromadb.app import app",
        "chromadb.cli.cli.app()",
        "chromadb_rust_bindings.cli(['chroma', 'run'])",
        "client = chromadb.HttpClient(host='0.0.0.0')",
        "chromadb.PersistentClient(path='/tmp/x')",
        "chromadb.EphemeralClient()",
        "chromadb.Client()",
        "uvicorn.run(app, port=8000)",
        "CMD chroma run --path /data",
    ):
        assert CHROMADB_SERVER_OR_CLIENT.search(line), line
    assert not CHROMADB_SERVER_OR_CLIENT.search('LOGGERS = ("crewai", "chromadb")')
