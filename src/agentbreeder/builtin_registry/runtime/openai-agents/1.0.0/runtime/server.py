"""runtime/openai-agents@1.0.0: agent protocol v1, kit profile (docs/agent-protocol.md).

The runtime kit (`kit/`, a byte copy of kit 1.0.0) serves the protocol, runs the turn and the
loopback model proxy; `openai_agents_adapter.py` binds the OpenAI Agents SDK to it.
"""

import os
import sys
from pathlib import Path

# Nothing goes to OpenAI: every OPENAI_* variable is removed before the SDK is imported, whatever
# the environment says (spec §8.1). Without OPENAI_API_KEY the SDK's own trace exporter has no
# key and skips its upload, and the instrumentor replaces that exporter anyway (exclusive
# processor). OPENAI_BASE_URL and OPENAI_AGENTS_DISABLE_TRACING cannot redirect the model client
# or switch the OpenInference spans off either.
for _name in [n for n in os.environ if n.startswith("OPENAI_")]:
    del os.environ[_name]

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kit import server  # noqa: E402
from openai_agents_adapter import LOGGERS, OpenAIAgentsAdapter  # noqa: E402

RUNTIME_REF = "runtime/openai-agents@1.0.0"
FRAMEWORK = "openai-agents"

if __name__ == "__main__":
    server.main(OpenAIAgentsAdapter, runtime_ref=RUNTIME_REF, framework=FRAMEWORK, loggers=LOGGERS)
