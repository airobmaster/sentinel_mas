# Airflow 3 with Sentinel installed in its own virtual environment (/opt/sentinel), so Sentinel's
# packages never clash with Airflow's pinned ones. DAG tasks call the `sentinel` CLI from it.
# DAGs and data are baked in for AWS; docker-compose.yml mounts the working copies over them locally.
FROM apache/airflow:3.3.2-python3.11

USER root
RUN python -m venv /opt/sentinel && chown -R airflow: /opt/sentinel \
    && mkdir -p /opt/sentinel-home && chown airflow: /opt/sentinel-home
USER airflow

COPY --chown=airflow requirements-service.txt requirements-airflow.txt /tmp/
RUN /opt/sentinel/bin/pip install --no-cache-dir -r /tmp/requirements-airflow.txt

COPY --chown=airflow pyproject.toml /opt/sentinel-src/pyproject.toml
COPY --chown=airflow src /opt/sentinel-src/src
RUN /opt/sentinel/bin/pip install --no-cache-dir --no-deps /opt/sentinel-src

ENV SENTINEL_HOME=/opt/sentinel-home
COPY --chown=airflow ingestion/airflow/dags /opt/airflow/dags
COPY --chown=airflow data/fixtures /opt/sentinel-home/data/fixtures
COPY --chown=airflow data/policies /opt/sentinel-home/data/policies
COPY --chown=airflow evals/datasets /opt/sentinel-home/evals/datasets
COPY --chown=airflow evals/thresholds.yaml /opt/sentinel-home/evals/thresholds.yaml
RUN /opt/sentinel/bin/sentinel data generate
