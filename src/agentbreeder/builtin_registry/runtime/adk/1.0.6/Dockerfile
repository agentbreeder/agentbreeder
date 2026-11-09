# AgentBreeder runtime/adk@1.0.6. Copied unchanged into the build context by `agentbreeder build`;
# it interpolates nothing. Base image pinned by digest (python:3.12-slim, multi-arch index).
FROM python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
WORKDIR /app
COPY runtime/requirements.lock /app/runtime/requirements.lock
# Every package is pinned and hash-checked (generated with uv; see the lock header).
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r /app/runtime/requirements.lock
COPY runtime/ /app/runtime/
COPY artifacts/ /app/artifacts/
COPY agent.config.json /app/agent.config.json
# No secrets or arguments in the image: secrets arrive as read-only files at deploy
# (DATABASE_URL_FILE, LLM_GATEWAY_KEY_FILE); services as plain AGENTBREEDER_* env vars.
ENV HOST=0.0.0.0 PORT=8080 HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
CMD ["python", "/app/runtime/server.py"]
