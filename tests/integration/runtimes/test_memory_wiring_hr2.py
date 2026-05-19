"""HR-2 / #404 smoke tests — memory wiring in Claude SDK / OpenAI Agents / CrewAI runtimes.

These confirm the source-code wiring is in place (mirrors langgraph_server.py).
End-to-end execution of the templates requires the framework SDKs + an agent
module on disk, which isn't part of the unit-test path. See the audit spec
docs/superpowers/specs/2026-05-18-platform-audit-design.md §3 (MM2) for the
deeper integration test plan.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "engine" / "runtimes" / "templates"


@pytest.mark.parametrize(
    "template_name",
    ["claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"],
)
def test_template_declares_memory_module_global(template_name: str) -> None:
    source = (TEMPLATES_DIR / template_name).read_text()
    assert "_memory" in source, f"{template_name} must declare the _memory global"
    assert "MemoryManager" in source, f"{template_name} must reference MemoryManager (HR-2)"


@pytest.mark.parametrize(
    "template_name",
    ["claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"],
)
def test_template_startup_initialises_memory(template_name: str) -> None:
    source = (TEMPLATES_DIR / template_name).read_text()
    # Startup must call .connect() and gate on ImportError.
    assert "await _memory.connect()" in source, (
        f"{template_name} startup must connect MemoryManager"
    )
    assert "except ImportError" in source, (
        f"{template_name} must gracefully handle missing memory_manager"
    )


@pytest.mark.parametrize(
    "template_name",
    ["claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"],
)
def test_template_invoke_uses_memory(template_name: str) -> None:
    source = (TEMPLATES_DIR / template_name).read_text()
    assert "await _memory.load(" in source, (
        f"{template_name} invoke must load prior conversation history"
    )
    assert "await _memory.save(" in source, f"{template_name} invoke must persist the new turn"


@pytest.mark.parametrize(
    "template_name",
    ["claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"],
)
def test_template_shutdown_closes_memory(template_name: str) -> None:
    source = (TEMPLATES_DIR / template_name).read_text()
    assert "await _memory.close()" in source, f"{template_name} shutdown must close MemoryManager"


def test_langgraph_reference_still_has_memory_wiring() -> None:
    """No regression in the reference implementation."""
    source = (TEMPLATES_DIR / "langgraph_server.py").read_text()
    assert "MemoryManager" in source
    assert "await _memory.connect()" in source
    assert "await _memory.load(" in source
    assert "await _memory.save(" in source
    assert "await _memory.close()" in source


def test_invoke_handlers_resolve_thread_id() -> None:
    """All 3 templates accept thread_id (or session_id) from request.config."""
    for name in ("claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"):
        source = (TEMPLATES_DIR / name).read_text()
        assert 'get("thread_id")' in source, f"{name} must resolve thread_id from config"
        # uuid fallback so a fresh request without a thread_id still namespaces saves.
        assert "uuid.uuid4()" in source, f"{name} must generate a uuid when no thread_id"


def test_memory_calls_are_guarded_against_load_failures() -> None:
    """If memory.load raises, the invoke handler must still serve the request."""
    for name in ("claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"):
        source = (TEMPLATES_DIR / name).read_text()
        # The handler wraps memory.load in a try/except so a backend hiccup
        # does not 500 the agent.
        assert "memory.load failed" in source, f"{name} must log + swallow memory.load failures"


def test_memory_calls_are_guarded_against_save_failures() -> None:
    """If memory.save raises, the response must still be returned to the caller."""
    for name in ("claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"):
        source = (TEMPLATES_DIR / name).read_text()
        assert "memory.save failed" in source, f"{name} must log + swallow memory.save failures"


def test_memory_module_globals_use_any_type() -> None:
    """_memory variables are typed Any so MemoryManager imports stay optional."""
    for name in ("claude_sdk_server.py", "openai_agents_server.py", "crewai_server.py"):
        source = (TEMPLATES_DIR / name).read_text()
        assert "_memory: Any = None" in source, (
            f"{name} must declare _memory: Any = None at module top"
        )


def test_memory_helpers_loadable() -> None:
    """The memory_manager helper module under templates/ remains importable."""
    helper = TEMPLATES_DIR / "memory_manager.py"
    assert helper.exists(), "engine/runtimes/templates/memory_manager.py must exist"
    spec = inspect.getsource(
        __import__("engine.runtimes.templates.memory_manager", fromlist=["*"])
    )
    assert "class MemoryManager" in spec
