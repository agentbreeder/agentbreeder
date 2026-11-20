"""runtime/deepagents@1.0.0: agent protocol v1, kit profile (docs/agent-protocol.md).

The runtime kit (`kit/`, a byte copy of kit 1.0.0) serves the protocol, runs the turn and the
loopback model proxy; `deepagents_adapter.py` binds Deep Agents to it.
"""

import os
import sys
from pathlib import Path

# LangSmith never: its and LangChain's tracing variables are removed before LangChain is
# imported, so nothing is sent to LangSmith whatever the environment says (spec §8.2, §8.3).
for _name in [n for n in os.environ if n.startswith(("LANGSMITH_", "LANGCHAIN_"))]:
    del os.environ[_name]

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deepagents_adapter import LOGGERS, DeepAgentsAdapter  # noqa: E402
from kit import server  # noqa: E402

RUNTIME_REF = "runtime/deepagents@1.0.0"
FRAMEWORK = "deepagents"

if __name__ == "__main__":
    server.main(DeepAgentsAdapter, runtime_ref=RUNTIME_REF, framework=FRAMEWORK, loggers=LOGGERS)
