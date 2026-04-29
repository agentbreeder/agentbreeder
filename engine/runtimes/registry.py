"""Language-based runtime registry.

Maps language strings to runtime factories. Adding a new language = one new
factory function + one dict entry. Zero changes to the deploy pipeline.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.config_parser import AgentConfig
from engine.runtimes.base import RuntimeBuilder


class UnsupportedLanguageError(Exception):
    """Raised when an agent config requests a language not yet in the registry."""


def _python_factory(config: AgentConfig) -> RuntimeBuilder:
    from engine.runtimes.python import PythonRuntimeFamily

    return PythonRuntimeFamily.from_framework(config.framework)


def _node_factory(config: AgentConfig) -> RuntimeBuilder:  # noqa: PLC0415
    from engine.runtimes.node import NodeRuntimeFamily

    return NodeRuntimeFamily()


def _go_factory(config: AgentConfig) -> RuntimeBuilder:  # noqa: PLC0415
    from engine.runtimes.go import GoRuntimeFamily

    return GoRuntimeFamily()


LANGUAGE_REGISTRY: dict[str, Callable[[AgentConfig], RuntimeBuilder]] = {
    "python": _python_factory,
    "node": _node_factory,
    "go": _go_factory,
}


def get_runtime_from_config(config: AgentConfig) -> RuntimeBuilder:
    """Route an AgentConfig to the correct RuntimeBuilder.

    If config.runtime is set, dispatches by language.
    Otherwise falls back to the Python path (config.framework).
    """
    if config.runtime:
        factory = LANGUAGE_REGISTRY.get(config.runtime.language)
        if factory is None:
            raise UnsupportedLanguageError(
                f"Language '{config.runtime.language}' is not yet supported. "
                f"Supported languages: {list(LANGUAGE_REGISTRY.keys())}"
            )
        return factory(config)
    return LANGUAGE_REGISTRY["python"](config)
