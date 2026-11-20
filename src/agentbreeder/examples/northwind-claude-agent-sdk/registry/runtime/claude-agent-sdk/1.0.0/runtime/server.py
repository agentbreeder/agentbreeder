"""runtime/claude-agent-sdk@1.0.0: agent protocol v1, kit profile (docs/agent-protocol.md).

One image, two roles (the two-uid contract, owner decision D15; `link_protocol.py`):
- the agent container (KIT_UID, the secret files): the runtime kit (`kit/`, a byte copy of kit
  1.0.0) serves the protocol, runs the turn and the model proxy on the Anthropic Messages route
  (`anthropic_proxy.py`), and `claude_adapter.py` binds the Claude Agent SDK to it;
- the CLI container (CLI_UID, no secret, `AGENTBREEDER_ROLE=cli-host`): `cli_host.py` starts one
  Claude Code CLI per turn for the kit.

AgentBreeder does not ship this runtime (owner decision D8): see docs/runtimes/claude-agent-sdk.md.
"""

import os
import signal
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link_protocol import HOST_ROLE, MODEL, ROLE_ENV, kit_dir  # noqa: E402

RUNTIME_REF = "runtime/claude-agent-sdk@1.0.0"
FRAMEWORK = "claude-agent-sdk"


def build_kit():
    """As the kit's `server.main` builds it, with this runtime's proxy."""
    from anthropic_proxy import AnthropicProxy
    from claude_adapter import ClaudeAdapter
    from kit import config as cfg
    from kit import telemetry
    from kit.turn import Kit

    config = cfg.config()
    telemetry.setup(
        config["name"], cfg.service("traces_url"), enabled=config.get("traces", "otlp") == "otlp"
    )
    key = cfg.secret("LLM_GATEWAY_KEY")
    proxy = AnthropicProxy(cfg.gateway_url(), key, kit_dir() / MODEL, config["model"]["alias"])
    return Kit(
        config,
        cfg.secret("DATABASE_URL"),
        key,
        ClaudeAdapter(),
        runtime_ref=RUNTIME_REF,
        framework=FRAMEWORK,
        proxy=proxy,
    )


def serve() -> None:
    from claude_adapter import LOGGERS
    from kit import logs
    from kit.server import App, make_handler

    logs.quiet(LOGGERS)
    app = App(build_kit)
    server = ThreadingHTTPServer(
        (os.getenv("HOST", "0.0.0.0"), int(os.getenv("PORT", "8080"))), make_handler(app)
    )
    threading.Thread(target=app.start, name="start", daemon=True).start()

    def stop():
        try:
            app.drain()  # the adapter's close() closes the link: the host ends every CLI
        finally:
            server.shutdown()

    signal.signal(
        signal.SIGTERM,
        lambda signum, frame: threading.Thread(target=stop, name="shutdown", daemon=True).start(),
    )
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    if os.environ.get(ROLE_ENV) == HOST_ROLE:
        import cli_host

        cli_host.main()
    else:
        serve()
