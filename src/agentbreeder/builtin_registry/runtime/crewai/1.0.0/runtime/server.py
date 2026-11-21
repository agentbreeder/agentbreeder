"""runtime/crewai@1.0.0: agent protocol v1, kit profile (docs/agent-protocol.md).

The runtime kit (`kit/`, a byte copy of kit 1.0.0) serves the protocol, runs the turn and the
loopback model proxy; `crewai_adapter.py` binds CrewAI to it.
"""

import os
import sys
import warnings
from pathlib import Path

# Before CrewAI is imported (spec §8.5):
# - no CREWAI_* or OPENAI_* setting from the environment reaches CrewAI or the OpenAI SDK (a
#   platform token, another base URL or key);
# - telemetry, tracking and trace upload (a separate switch) off. Never OTEL_SDK_DISABLED, which
#   CrewAI also honours and which would stop the kit's own spans;
# - a fixed storage directory name, so CrewAI's paths do not depend on the working directory
#   (`crewai_confine.py` keeps the task-output store off and sweeps it).
for _name in [n for n in os.environ if n.startswith(("CREWAI_", "OPENAI_"))]:
    del os.environ[_name]
os.environ.update(
    CREWAI_DISABLE_TELEMETRY="true",
    CREWAI_DISABLE_TRACKING="true",
    CREWAI_TRACING_ENABLED="false",
    CREWAI_STORAGE_DIR="agentbreeder",
)

# Python warnings can carry a library's view of the data (a pydantic `input_value=`); none reaches
# the log, which holds only the kit's one-line, redacted lines.
warnings.simplefilter("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crewai_confine  # noqa: E402
from kit import server  # noqa: E402

crewai_confine.confine()

from crewai_adapter import LOGGERS, CrewAIAdapter  # noqa: E402

RUNTIME_REF = "runtime/crewai@1.0.0"
FRAMEWORK = "crewai"

if __name__ == "__main__":
    server.main(CrewAIAdapter, runtime_ref=RUNTIME_REF, framework=FRAMEWORK, loggers=LOGGERS)
