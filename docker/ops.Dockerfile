# Supporting images for AWS, with their configuration baked in (ECS has no bind mounts). Build one target:
#   docker build -f docker/ops.Dockerfile --target grafana -t sentinel-ops:grafana .
# Locally docker-compose.yml runs the upstream images with the same files mounted.

FROM openpolicyagent/opa:latest AS opa
COPY guardrails/opa /policies
CMD ["run", "--server", "--addr", "0.0.0.0:8181", "--log-level", "info", "/policies"]

FROM otel/opentelemetry-collector-contrib:latest AS otel
COPY observability/otel-collector.yaml /etc/otelcol/config.yaml
CMD ["--config=/etc/otelcol/config.yaml"]

FROM grafana/tempo:2.8.2 AS tempo
COPY observability/tempo.yaml /etc/tempo.yaml
CMD ["-config.file=/etc/tempo.yaml", "-config.expand-env=true"]

FROM prom/prometheus:latest AS prometheus
COPY observability/prometheus-aws.yml /etc/prometheus/prometheus.yml

FROM grafana/grafana:latest AS grafana
COPY observability/grafana/provisioning /etc/grafana/provisioning
COPY observability/grafana/dashboards /var/lib/grafana/dashboards
