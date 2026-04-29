"""Go runtime family — Tier-2 polyglot SDK target.

Builds Go agent containers using the AgentBreeder Go SDK
(``sdk/go/agentbreeder``). Currently only the ``custom`` framework is
shipped; future framework templates (eino, genkit, dapr_agents,
langchaingo, anthropic_go_sdk) hang off this module.
"""

from __future__ import annotations

from engine.runtimes.go.builder import GoRuntimeFamily

__all__ = ["GoRuntimeFamily"]
