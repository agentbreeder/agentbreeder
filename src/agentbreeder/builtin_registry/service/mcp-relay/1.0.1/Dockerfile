# AgentBreeder service/mcp-relay@1.0.1: standard-library Python only, nothing installed.
# Base image pinned by digest (python:3.12-slim, multi-arch index, as the egress sidecar).
FROM python@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
COPY relay/relay.py /opt/relay/relay.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python3", "/opt/relay/relay.py"]
