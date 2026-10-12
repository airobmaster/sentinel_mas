# Sentinel service image: the API (`sentinel api`), the Kafka workers (`sentinel worker all`) and one-off
# tasks on AWS (`sentinel kafka init`, `sentinel data load|kb|graph`).
#   docker compose up -d --build api      (see docker-compose.yml)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SENTINEL_HOME=/app

COPY requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements-service.txt

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
# Data the tasks and the replay need: fixtures, policies, the golden set, thresholds; the synthetic
# dataset is regenerated from its seed
COPY data/fixtures ./data/fixtures
COPY data/policies ./data/policies
COPY evals/datasets ./evals/datasets
COPY evals/thresholds.yaml ./evals/thresholds.yaml
RUN sentinel data generate
ENV SENTINEL_POLICY_DIR=/app/data/policies

RUN useradd --create-home --uid 10001 sentinel && mkdir -p evals/results && chown sentinel evals/results
USER sentinel
EXPOSE 8000 9464
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
ENTRYPOINT ["sentinel"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
