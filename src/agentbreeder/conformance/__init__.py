"""The conformance kit for agent images (#759, multi-framework spec §10.3).

`agentbreeder conformance IMAGE` runs a black-box suite against an image and writes a report
with one row per check. This package is imported by name into the harness container
(`fakes.py`), so this file imports nothing.

- `protocol`: agent protocol v1 as data (docs/agent-protocol.md is the prose).
- `fakes`: the scripted fake gateway, span sink and ingress forwarder (standard library only).
- `checks`: the checks, against any agent URL and any `fakes` control URL.
- `environment`: the local Docker environment the command builds around the image.
- `report`: the report, where it is written and how it is read back.
"""
