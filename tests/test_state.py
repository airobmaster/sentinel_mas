from sentinel.state import merge_dicts, merge_evidence


def ev(eid: str, summary: str = "s") -> dict:
    return {"id": eid, "source": "test", "agent": "txn", "summary": summary}


def test_merge_evidence_upserts_by_id():
    merged = merge_evidence([ev("txn:A", "old"), ev("txn:B")], [ev("txn:A", "new"), ev("txn:C")])
    assert sorted(e["id"] for e in merged) == ["txn:A", "txn:B", "txn:C"]
    assert next(e for e in merged if e["id"] == "txn:A")["summary"] == "new"


def test_merge_evidence_handles_empty():
    assert merge_evidence(None, [ev("txn:A")]) == [ev("txn:A")]
    assert merge_evidence([ev("txn:A")], None) == [ev("txn:A")]


def test_merge_dicts_keeps_other_agents():
    assert merge_dicts({"txn": 1}, {"kyc": 2}) == {"txn": 1, "kyc": 2}
    assert merge_dicts({"txn": 1}, {"txn": 3}) == {"txn": 3}
