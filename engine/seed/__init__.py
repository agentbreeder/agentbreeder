"""Engine seed module — first-boot registry seeding.

This package populates empty registry tables (agents, prompts, tools,
mcp_servers, providers, knowledge_bases) with starter content from
``examples/seed/`` so a fresh deploy doesn't show empty dashboard pages.

Public entry point: :func:`seed_registries`.
"""

from __future__ import annotations

from engine.seed.first_boot import SeedReport, seed_registries

__all__ = ["SeedReport", "seed_registries"]
