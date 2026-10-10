"""Sentinel batch jobs (Airflow 3). Each task calls the `sentinel` CLI from its own virtual environment
in the image (docker/airflow.Dockerfile), so the same commands run from a terminal or from Airflow.

- alert_replay      golden-set alerts -> Kafka -> workers -> scored against ground truth (on demand)
- sanctions_refresh reload the sanctions and PEP lists in one transaction (daily)
- graph_rebuild     reload the customer network into Neo4j and recompute mule scores (daily, after lists)
- policy_reembed    re-embed the policy manuals into the pgvector knowledge base (on demand)

The Kafka workers (`sentinel worker all`) must be running for alert_replay: they run the agents.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

SENTINEL = "/opt/sentinel/bin/sentinel"
START = datetime(2026, 1, 1)
DEFAULTS = {"owner": "sentinel", "retries": 1, "retry_delay": timedelta(minutes=2)}

with DAG(
    "alert_replay",
    description="Replay golden-set alerts through Kafka and score the results",
    schedule=None,
    start_date=START,
    catchup=False,
    default_args=DEFAULTS,
    tags=["sentinel", "evaluation", "demo"],
    params={
        "split": Param("dev", enum=["dev", "holdout", "all"], description="Golden-set split"),
        "n": Param(5, type="integer", minimum=1, maximum=100, description="Cases, balanced across typologies"),
        "timeout_minutes": Param(120, type="integer", minimum=5, maximum=600),
    },
    doc_md=__doc__,
) as alert_replay:
    select = BashOperator(
        task_id="select_cases",
        bash_command=f"{SENTINEL} replay select --split {{{{ params.split }}}} --n {{{{ params.n }}}}",
        do_xcom_push=True,  # last line of output: the comma-separated case IDs
    )
    cases = "{{ ti.xcom_pull(task_ids='select_cases') }}"
    publish = BashOperator(task_id="publish_alerts", bash_command=f"{SENTINEL} replay publish {cases}",
                           retries=0)  # publishing resets the cases; never repeat it automatically
    wait = BashOperator(
        task_id="wait_for_review",
        bash_command=f"{SENTINEL} replay wait {cases} --timeout {{{{ params.timeout_minutes * 60 }}}}",
        execution_timeout=timedelta(hours=10),
        retries=0,
    )
    score = BashOperator(task_id="score",
                         bash_command=f"{SENTINEL} replay score {cases} --split {{{{ params.split }}}}")
    select >> publish >> wait >> score

with DAG(
    "nightly_eval",
    description="Nightly quality check: held-out cases through Kafka, scored and judged against the thresholds",
    schedule="0 2 * * *",
    start_date=START,
    catchup=False,
    default_args=DEFAULTS,
    tags=["sentinel", "evaluation"],
    params={"n": Param(6, type="integer", minimum=1, maximum=20, description="Held-out cases")},
    doc_md="Replays held-out golden cases through Kafka (the workers must be running), then runs the quality "
           "gate on the results: outcome metrics plus the narrative rubric (DeepEval G-Eval, Claude Haiku). The "
           "`gate` task fails when a threshold in evals/thresholds.yaml is missed. Scores land in `evals.runs` "
           "(Grafana: Sentinel · Case KPIs).",
) as nightly_eval:
    select_nightly = BashOperator(task_id="select_cases", do_xcom_push=True,
                                  bash_command=f"{SENTINEL} replay select --split holdout --n {{{{ params.n }}}}")
    nightly_cases = "{{ ti.xcom_pull(task_ids='select_cases') }}"
    publish_nightly = BashOperator(task_id="publish_alerts", bash_command=f"{SENTINEL} replay publish {nightly_cases}",
                                   retries=0)
    wait_nightly = BashOperator(task_id="wait_for_review", retries=0, execution_timeout=timedelta(hours=4),
                                bash_command=f"{SENTINEL} replay wait {nightly_cases} --timeout 10800")
    gate_nightly = BashOperator(task_id="gate", retries=0,
                                bash_command=f"{SENTINEL} gate --from-runs --split holdout --cases {nightly_cases}")
    select_nightly >> publish_nightly >> wait_nightly >> gate_nightly

with DAG(
    "sanctions_refresh",
    description="Reload the sanctions and PEP lists",
    schedule="0 5 * * *",
    start_date=START,
    catchup=False,
    default_args=DEFAULTS,
    tags=["sentinel", "data"],
) as sanctions_refresh:
    BashOperator(task_id="refresh_lists", bash_command=f"{SENTINEL} data lists")

with DAG(
    "graph_rebuild",
    description="Reload the customer network into Neo4j and recompute GDS scores",
    schedule="30 5 * * *",
    start_date=START,
    catchup=False,
    default_args=DEFAULTS,
    tags=["sentinel", "data"],
) as graph_rebuild:
    BashOperator(task_id="load_graph", bash_command=f"{SENTINEL} data graph", execution_timeout=timedelta(minutes=30))

with DAG(
    "policy_reembed",
    description="Re-embed the policy manuals into the knowledge base (Bedrock embeddings)",
    schedule=None,
    start_date=START,
    catchup=False,
    default_args=DEFAULTS,
    tags=["sentinel", "data"],
) as policy_reembed:
    BashOperator(task_id="embed_policies", bash_command=f"{SENTINEL} data kb", execution_timeout=timedelta(minutes=30))
