"""Generates the Grafana dashboards (FR-151/152) into observability/grafana/dashboards/*.json.

    python observability/grafana/build_dashboards.py

Three dashboards: Platform (API, workers, agent steps, models, tools), Case KPIs (queue by level and
status, decisions, evaluation results) and Security (guardrail events). Grafana loads them on start.
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("dashboards")
PROM = {"type": "prometheus", "uid": "prometheus"}
PG = {"type": "grafana-postgresql-datasource", "uid": "postgres"}
SPANS = "sentinel_traces_traces_span_metrics"  # span metrics derived by the OTel collector


def panel(title, kind, targets, x, y, w=12, h=8, unit=None, desc="", options=None, field=None):
    return {"title": title, "type": kind, "description": desc, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": targets[0]["datasource"], "targets": targets,
            "fieldConfig": {"defaults": {**({"unit": unit} if unit else {}), **(field or {})}, "overrides": []},
            "options": options or {}}


def prom(expr, legend="", ref="A"):
    return {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref}


def sql(query, ref="A", fmt="table"):
    return {"datasource": PG, "rawSql": query, "format": fmt, "rawQuery": True, "editorMode": "code", "refId": ref}


def dashboard(uid, title, panels, refresh="30s", desc=""):
    return {"uid": uid, "title": title, "description": desc, "tags": ["sentinel"], "timezone": "browser",
            "schemaVersion": 39, "refresh": refresh, "time": {"from": "now-6h", "to": "now"},
            "panels": [{**p, "id": i + 1} for i, p in enumerate(panels)]}


platform = dashboard("sentinel-platform", "Sentinel · Platform", [
    panel("Agent step duration p95 (by step)", "timeseries", [prom(
        f'histogram_quantile(0.95, sum by (le, sentinel_node) (rate({SPANS}_duration_milliseconds_bucket'
        f'{{sentinel_kind="node"}}[15m])))', "{{sentinel_node}}")], 0, 0, unit="ms",
        desc="From the traces: time each graph step takes (KYC, Transactions, Screening, Network, Typology, ...)"),
    panel("Model calls per minute (by agent)", "timeseries", [prom(
        'sum by (agent) (rate(sentinel_model_calls_total[5m])) * 60', "{{agent}}")], 12, 0),
    panel("Model tokens per minute (by agent)", "timeseries", [prom(
        'sum by (agent) (rate(sentinel_model_tokens_total[5m])) * 60', "{{agent}}")], 0, 8),
    panel("Tool calls per minute (by tool)", "timeseries", [prom(
        'sum by (tool) (rate(sentinel_tool_calls_total[5m])) * 60', "{{tool}}")], 12, 8),
    panel("Model and tool errors", "timeseries", [
        prom('sum(rate(sentinel_model_calls_total{outcome="error"}[5m])) * 60', "model errors/min"),
        prom('sum(rate(sentinel_tool_calls_total{outcome="error"}[5m])) * 60', "tool errors/min", "B")], 0, 16, w=8),
    panel("API requests per second (by route)", "timeseries", [prom(
        'sum by (route) (rate(sentinel_api_requests_total[5m]))', "{{route}}")], 8, 16, w=8),
    panel("API latency p95 (by route)", "timeseries", [prom(
        'histogram_quantile(0.95, sum by (le, route) (rate(sentinel_api_request_seconds_bucket[5m])))',
        "{{route}}")], 16, 16, w=8, unit="s"),
    panel("Case events per minute (workers)", "timeseries", [prom(
        'sum by (type) (rate(sentinel_case_events_total[5m])) * 60', "{{type}}")], 0, 24, w=24),
], desc="API, workers, agent steps, model and tool calls")

kpis = dashboard("sentinel-kpis", "Sentinel · Case KPIs", [
    panel("Cases waiting, by level", "bargauge", [sql(
        "SELECT CASE assigned_role WHEN 'l1' THEN 'L1 analyst' WHEN 'l2' THEN 'L2 investigator' "
        "WHEN 'mlro' THEN 'MLRO' ELSE 'unassigned' END AS level, count(*) AS cases FROM cases.alerts "
        "WHERE status IN ('awaiting_review', 'awaiting_approval') GROUP BY 1 ORDER BY 1")], 0, 0, w=8,
        options={"orientation": "horizontal", "reduceOptions": {"values": True, "calcs": ["lastNotNull"]}}),
    panel("Cases by status", "piechart", [sql(
        "SELECT status, count(*) AS cases FROM cases.alerts GROUP BY status ORDER BY 2 DESC")], 8, 0, w=8,
        options={"reduceOptions": {"values": True, "calcs": ["lastNotNull"]}, "legend": {"displayMode": "table",
                                                                                      "placement": "right"}}),
    panel("Waiting longer than 1 hour", "stat", [sql(
        "SELECT count(*) AS cases FROM cases.alerts WHERE status IN ('awaiting_review', 'awaiting_approval') "
        "AND updated_at < now() - interval '1 hour'")], 16, 0, w=4,
        field={"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                            {"color": "orange", "value": 1},
                                                            {"color": "red", "value": 10}]}}),
    panel("QA-flagged cases", "stat", [sql("SELECT count(*) AS cases FROM cases.alerts WHERE qa_flagged")],
          20, 0, w=4, desc="Automated QA raised issues: always reviewed by QA (BR-18)"),
    panel("Decisions per hour (level:action)", "timeseries", [prom(
        'sum by (detail) (increase(sentinel_case_events_total{type="decision_applied"}[1h]))', "{{detail}}")],
        0, 8, w=12),
    panel("Tokens per case (latest evaluation runs)", "timeseries", [sql(
        "SELECT created_at AS time, (metrics->>'mean_tokens')::float AS tokens FROM evals.runs ORDER BY 1",
        fmt="time_series")], 12, 8, w=12),
    panel("Evaluation runs (golden set)", "table", [sql(
        "SELECT created_at AS \"run at\", source, split, (metrics->>'cases')::int AS cases, "
        "(metrics->>'escalation_recall')::float AS recall, (metrics->>'false_escalation_rate')::float "
        "AS \"false escalations\", (metrics->>'acceptable')::float AS acceptable, "
        "(metrics->>'citation_validity')::float AS citations, (metrics->>'injections_caught')::float "
        "AS injections, (metrics->>'errors')::int AS errors FROM evals.runs ORDER BY created_at DESC LIMIT 20")],
        0, 16, w=24, h=9),
], refresh="1m", desc="Work queue, decisions and evaluation results")

security = dashboard("sentinel-security", "Sentinel · Security", [
    panel("Injections caught", "stat", [prom('sum(increase(sentinel_security_events_total{kind="injection_detected"}[$__range]))')],
          0, 0, w=6),
    panel("Tool calls denied by OPA", "stat", [prom('sum(increase(sentinel_security_events_total{kind="tool_denied"}[$__range]))')],
          6, 0, w=6),
    panel("Budget limits hit", "stat", [prom(
        'sum(increase(sentinel_security_events_total{kind=~"budget_limit|budget_exceeded"}[$__range]))')], 12, 0, w=6),
    panel("Tool errors", "stat", [prom('sum(increase(sentinel_tool_calls_total{outcome="error"}[$__range]))')],
          18, 0, w=6),
    panel("Guardrail events over time (by kind)", "timeseries", [prom(
        'sum by (kind) (increase(sentinel_security_events_total[15m]))', "{{kind}}")], 0, 8, w=24),
], desc="Prompt-injection catches, OPA denials and budget limits (UC-08)")

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for board in (platform, kpis, security):
        (OUT / f"{board['uid']}.json").write_text(json.dumps(board, indent=2), encoding="utf-8")
        print("wrote", board["uid"])
