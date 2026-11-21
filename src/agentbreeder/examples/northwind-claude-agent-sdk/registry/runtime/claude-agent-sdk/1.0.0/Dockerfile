# runtime/claude-agent-sdk@1.0.0. Copied unchanged into the build context by `agentbreeder build`;
# it interpolates nothing. Base image pinned by digest (python:3.12-slim, multi-arch index), the
# same base as runtime/adk and runtime/langgraph. The claude-agent-sdk wheel, and the Claude Code
# CLI binary inside it, are downloaded from PyPI by this build on your machine; AgentBreeder does
# not ship them (owner decision D8).
FROM python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
LABEL org.agentbreeder.runtime="runtime/claude-agent-sdk@1.0.0" org.agentbreeder.protocol="1"
WORKDIR /app
COPY runtime/requirements.lock /app/runtime/requirements.lock
# Every package is pinned and hash-checked (generated with uv; see the lock header).
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r /app/runtime/requirements.lock
# The mount point of the directory the agent and CLI containers share (two-uid contract).
RUN mkdir -p /run/agentbreeder-cli
COPY runtime/ /app/runtime/
COPY artifacts/ /app/artifacts/
COPY agent.config.json /app/agent.config.json
# No secrets or arguments in the image: secrets arrive as read-only files at deploy, in the agent
# container only (DATABASE_URL_FILE, LLM_GATEWAY_KEY_FILE); services as plain AGENTBREEDER_* env
# vars. The deploy starts the CLI container from this image as uid 65533 with
# AGENTBREEDER_ROLE=cli-host.
ENV HOST=0.0.0.0 PORT=8080 HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/runtime/server.py"]
