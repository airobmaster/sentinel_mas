# Airflow 3 with Sentinel installed in its own virtual environment (/opt/sentinel), so Sentinel's
# packages never clash with Airflow's pinned ones. DAG tasks call the `sentinel` CLI from it.
FROM apache/airflow:3.3.2-python3.11

USER root
RUN python -m venv /opt/sentinel && chown -R airflow: /opt/sentinel
USER airflow

COPY --chown=airflow requirements-service.txt requirements-airflow.txt /tmp/
RUN /opt/sentinel/bin/pip install --no-cache-dir -r /tmp/requirements-airflow.txt

COPY --chown=airflow pyproject.toml /opt/sentinel-src/pyproject.toml
COPY --chown=airflow src /opt/sentinel-src/src
RUN /opt/sentinel/bin/pip install --no-cache-dir --no-deps /opt/sentinel-src
