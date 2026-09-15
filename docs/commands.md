# Focused rebuild reference

Use a fresh virtual environment. Install `python -m pip install -e ".[dev]"`. Run `agentbreeder --help`, `agentbreeder validate examples/http-agent/agent.yaml`, `agentbreeder doctor`, `python -m pytest`, `ruff check src tests examples/http-agent`, and `python -m build`. Local `deploy MANIFEST --local`, `status ID`, and `remove ID --local` are implemented; see the root README.
