FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
USER 65532:65532
ENV HOST=0.0.0.0 PORT=8080 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python", "/app/server.py"]
