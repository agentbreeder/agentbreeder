"""AgentBreeder's runtime kit (#1083 F2b, multi-framework spec §7).

The shared core of every kit runtime (`profile: kit` in a runtime manifest): protocol v1 HTTP
(`server.py`), the turn (`turn.py`), the loopback model proxy (`proxy.py`, with the model-error
rules in `errors.py`), tools (`tools.py`), knowledge (`rag.py`), memory (`memory.py`), traces
(`telemetry.py`) and redaction. A framework adapter (`adapter.py`) only translates between this
core and one framework.

This is the source of truth. Each kit runtime version vendors a byte copy as `runtime/kit/`
(D2), and `tests/test_runtime_kit_vendored.py` checks the copy against its manifest's `kit`
version. The package imports nothing from `agentbreeder` (it is never installed as the
control-plane package in an image) and its modules import each other relatively, so the same
bytes work as `agentbreeder.runtime_kit` here and as `kit` in an image.

Taken from `runtime/adk@1.3.0`, reimplemented over raw HTTP where 1.3.0 read LiteLLM objects;
`tests/test_runtime_kit_parity.py` compares the model-error rules with 1.3.0's own functions.
"""

KIT_VERSION = "1.0.0"
