FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "print('ok')"
CMD ["adk", "api_server", "--host", "0.0.0.0", "--port", "8080"]
