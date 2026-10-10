"""LangSmith experiments (D6-05, development only: synthetic data, BR-14).

The golden set is a LangSmith dataset ("sentinel-golden", one example per case, tagged dev or holdout).
An experiment runs a slice of it through the graph in this process (up to human review) and scores each case,
so prompt or model versions can be compared side by side in LangSmith:

    sentinel experiment --split dev --n 10 --name narrative-v1.4
"""

import os

from sentinel import data
from sentinel.config import settings
from sentinel.evaluate import golden_cases, scored, select_cases
from sentinel.graph import compile_graph, initial_state, run_config

DATASET = "sentinel-golden"


def client():
    from langsmith import Client

    if not os.environ.get("LANGSMITH_API_KEY"):
        raise RuntimeError("set LANGSMITH_API_KEY (and LANGSMITH_PROJECT) in .env")
    return Client()


def sync_dataset() -> dict[str, str]:
    """Create the dataset if needed and add any golden case not in it yet. Returns case_id -> example id."""
    ls = client()
    dataset = (ls.read_dataset(dataset_name=DATASET) if ls.has_dataset(dataset_name=DATASET) else
               ls.create_dataset(DATASET, description="Sentinel golden set: alerts with known outcomes (synthetic)"))
    existing = {e.inputs["case_id"]: str(e.id) for e in ls.list_examples(dataset_id=dataset.id)}
    truth, holdout = data.ground_truth(), set(golden_cases("holdout"))
    new = [c for c in golden_cases("all") if c not in existing]
    if new:
        created = ls.create_examples(
            dataset_id=dataset.id,
            inputs=[{"case_id": c} for c in new],
            outputs=[{k: truth[c][k] for k in ("expected", "acceptable", "typology")} for c in new],
            metadata=[{"split": "holdout" if c in holdout else "dev", "typology": truth[c]["typology"]} for c in new])
        ids = created.get("example_ids", []) if isinstance(created, dict) else [str(e.id) for e in created]
        existing |= dict(zip(new, ids))
    return existing


def acceptable(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "acceptable", "score": int(outputs.get("recommendation") in reference_outputs["acceptable"])}


def escalation_recall(outputs: dict, reference_outputs: dict) -> dict | None:
    if reference_outputs["acceptable"] != ["escalate"]:
        return None  # only must-escalate cases count towards recall
    return {"key": "escalated", "score": int(outputs.get("recommendation") == "escalate")}


def citations_valid(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "citations_valid", "score": int(outputs.get("bad_citations", 1) == 0)}


async def run(split: str, n: int, name: str, concurrency: int = 3) -> str:
    """Run the experiment; returns its name in LangSmith."""
    from langsmith import aevaluate

    ids = sync_dataset()
    cases = select_cases(split, n)
    graph = compile_graph()
    alerts = {c: data.get_alert(c) for c in cases}

    async def target(inputs: dict) -> dict:
        case_id = inputs["case_id"]
        config = run_config(f"{case_id}:exp-{name}")
        await graph.ainvoke(initial_state(alerts[case_id]), config)
        row = scored((await graph.aget_state(config)).values)
        return {k: row[k] for k in ("recommendation", "reason_code", "tier", "bad_citations", "tokens")}

    ls = client()
    examples = [ls.read_example(ids[c]) for c in cases]
    results = await aevaluate(
        target, data=examples, evaluators=[acceptable, escalation_recall, citations_valid],
        experiment_prefix=name, max_concurrency=concurrency, client=ls,
        metadata={"split": split, "cases": len(cases), "models": {a: getattr(settings, f"model_{a}") for a in
                                                                  ("kyc", "txn", "screening", "typology", "narrative")}})
    return results.experiment_name
