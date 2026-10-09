# Sentinel service image: the API (`sentinel api`), and the Kafka workers once they move to AWS.
#   docker compose up -d --build api      (see docker-compose.yml)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements-service.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY data/policies ./data/policies
ENV SENTINEL_POLICY_DIR=/app/data/policies

RUN useradd --create-home --uid 10001 sentinel
USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
ENTRYPOINT ["sentinel"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
