"""First-party AgentBreeder tools — generic, reusable across agents.

Each module exports a single function with the same name as the file (snake_case)
plus a ``SCHEMA`` dict describing its parameters. The tool resolver
(``engine.tool_resolver.resolve_tool``) finds these via ``tools/<kebab-name>``
references in agent.yaml.

Available tools:

- ``web_search``       — Tavily-backed web search (returns sources + answer).
- ``markdown_writer``  — Persists arbitrary markdown content to disk.

NOTE: Functions are intentionally **not** re-exported at the package level.
Re-exporting would shadow the submodules of the same name (Python import quirk),
breaking ``import engine.tools.standard.web_search as ws_mod`` -style access
that registry-seeding scripts use to read the SCHEMA. Always import the full
module path.
"""
