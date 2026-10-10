from sentinel.evaluate import metrics, stratified_sample


def row(expected, acceptable, got, bad=0):
    return {"expected": expected, "acceptable": acceptable, "recommendation": got, "bad_citations": bad,
            "citations": 5, "seconds": 10}


def test_stratified_sample_covers_every_typology_first():
    alerts = [{"case_id": f"C{i}"} for i in range(6)]
    esc, close = "escalate", "close"
    truth = {"C0": {"typology": "A", "expected": esc}, "C1": {"typology": "A", "expected": esc},
             "C2": {"typology": "A", "expected": esc}, "C3": {"typology": "B", "expected": esc},
             "C4": {"typology": "C", "expected": close}, "C5": {"typology": "C", "expected": close}}
    picked = [a["case_id"] for a in stratified_sample(alerts, truth, 4)]
    assert picked == ["C0", "C4", "C3", "C1"]  # escalate and benign typologies alternate


def test_metrics():
    rows = [
        row("escalate", ["escalate"], "escalate"),
        row("escalate", ["escalate"], "close"),  # missed escalation
        row("escalate", ["escalate", "request_info"], "request_info"),  # acceptable, not in recall set
        row("close", ["close"], "escalate", bad=1),  # false escalation, bad citation
        row("close", ["close", "request_info"], "close"),
        {"expected": "close", "acceptable": ["close"], "error": "boom", "seconds": 1},
    ]
    m = metrics(rows)
    assert m["cases"] == 6 and m["errors"] == 1
    assert m["escalation_recall"] == 0.5
    assert m["false_escalation_rate"] == 0.5
    assert m["agreement"] == 0.4 and m["acceptable"] == 0.6
    assert m["citation_validity"] == 0.96  # 1 bad of 25 citations
