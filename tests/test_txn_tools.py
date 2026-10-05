from sentinel.tools.txn_tools import detect_structuring, get_transactions, velocity_stats

AS_OF = "2026-03-31"


def call(tool, **args):
    """Invoke a content_and_artifact tool and return (content, artifact)."""
    msg = tool.invoke({"type": "tool_call", "id": "1", "name": tool.name, "args": args})
    return msg.content, msg.artifact


def test_get_transactions_respects_lookback():
    _, evidence = call(get_transactions, account_id="ACC-1001", as_of=AS_OF, lookback_days=90)
    ids = [e["id"] for e in evidence]
    assert "txn:TXN-1000" not in ids  # 2025-11-20 is outside the window
    assert ids[0] == "txn:TXN-1001" and ids[-1] == "txn:TXN-1014"
    assert all(e["source"] == "txn_history.get_transactions" for e in evidence)


def test_get_transactions_unknown_account():
    content, evidence = call(get_transactions, account_id="ACC-9999", as_of=AS_OF)
    assert evidence == [] and "No transactions" in content


def test_detect_structuring_finds_planted_pattern():
    content, evidence = call(detect_structuring, account_id="ACC-1001", as_of=AS_OF)
    assert "STRUCTURING PATTERN" in content
    assert [e["id"] for e in evidence] == [
        "txn:TXN-1006", "txn:TXN-1008", "txn:TXN-1009", "txn:TXN-1010", "txn:TXN-1013",
    ]
    assert "4 branches" in content


def test_detect_structuring_benign_account():
    content, evidence = call(detect_structuring, account_id="ACC-2001", as_of=AS_OF)
    assert evidence == [] and "No structuring pattern" in content


def test_velocity_stats():
    content, evidence = call(velocity_stats, account_id="ACC-1001", as_of=AS_OF)
    assert evidence[0]["id"] == "acct:ACC-1001"
    assert "cash credits 47,550.00" in content
    assert "cash_ratio 0.895" in content  # 47,550 / 53,100
