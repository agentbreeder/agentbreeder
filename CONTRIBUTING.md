# Contributing

This branch is an incompatible development rebuild. Use a fresh Python 3.11+ environment and the setup in README.md.

Keep changes within the supported deployment workflow. Do not add dormant APIs, simulated success paths, marketplace features or framework-specific agent behavior to the core.

Before implementation, record an engineering issue and spec with inputs, side effects, acceptance tests and compatibility implications. Follow the repository review and gate instructions in CLAUDE.md. Private planning/customer material must stay out of public issues and commits.

Tests live in `tests/`; source lives in `src/agentbreeder/`. Tests must exercise real contract behavior and failure handling. Use bounded mocks for external dependencies, not fabricated deployment evidence.

Run pytest, Ruff and a package build before proposing changes. Production deployment features additionally require real target validation, permissions tests and recovery evidence. Do not release this baseline as an upgrade to the legacy platform.
