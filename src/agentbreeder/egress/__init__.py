"""Phase 4: enforced MCP Streamable-HTTP egress boundary for local Docker deployments.

`protocol`, `sidecar` and `probe` use only the standard library because they run inside the
pinned sidecar image. `host` (the decision service) and `boundary` (Docker orchestration) run on
the host and may use the rest of AgentBreeder.
"""
