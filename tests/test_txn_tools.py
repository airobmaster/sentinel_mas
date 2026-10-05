import pytest
from langchain_core.tools import ToolException

from sentinel.tools.txn_tools import (
    TXN_TOOLS,
    detect_pass_through,
    detect_structuring,
    get_transactions,
    velocity_stats,
)

AS_OF = "2026-03-31"


def test_get_transactions_respects_lookback():
    _, evidence = get_transactions("UK", "ACC-1001", AS_OF, 90)
    ids = [e["id"] for e in evidence]
    assert "txn:TXN-1000" not in ids  # 2025-11-20 is outside the window
    assert ids[0] == "txn:TXN-1001" and ids[-1] == "txn:TXN-1014"
    assert all(e["source"] == "txn_history.get_transactions" for e in evidence)


def test_account_must_belong_to_legal_entity():
    with pytest.raises(ToolException, match="not found in legal entity ES"):
        get_transactions("ES", "ACC-1001", AS_OF)
    with pytest.raises(ToolException):
        get_transactions("UK", "ACC-9999", AS_OF)


def test_lookback_limit():
    with pytest.raises(ToolException, match="lookback_days"):
        velocity_stats("UK", "ACC-1001", AS_OF, 401)


def test_detect_structuring_finds_planted_pattern():
    content, evidence = detect_structuring("UK", "ACC-1001", AS_OF)
    assert "STRUCTURING PATTERN" in content
    assert [e["id"] for e in evidence] == [
        "txn:TXN-1006", "txn:TXN-1008", "txn:TXN-1009", "txn:TXN-1010", "txn:TXN-1013",
    ]
    assert "4 branches" in content


def test_detect_structuring_benign_account():
    content, evidence = detect_structuring("UK", "ACC-2001", AS_OF)
    assert [e["id"] for e in evidence] == ["check:structuring:ACC-2001"]  # negative result is citable
    assert "No structuring pattern" in content


def test_detect_pass_through_respects_max_days():
    # ACC-3001: 25,000 in -> 22,000 out after 4 days; 26,000 in -> 22,000 out after 5 days
    content, evidence = detect_pass_through("UK", "ACC-3001", AS_OF)
    assert [e["id"] for e in evidence] == ["check:pass_through:ACC-3001"] and "No pass-through pairs" in content
    content, evidence = detect_pass_through("UK", "ACC-3001", AS_OF, max_days=5)
    assert "PASS-THROUGH PATTERN" in content
    assert [e["id"] for e in evidence] == ["txn:TXN-3001", "txn:TXN-3002", "txn:TXN-3003", "txn:TXN-3004"]


def test_velocity_stats():
    content, evidence = velocity_stats("UK", "ACC-1001", AS_OF)
    assert evidence[0]["id"] == "acct:ACC-1001"
    assert "cash credits 47,550.00" in content
    assert "cash_ratio 0.895" in content  # 47,550 / 53,100


def test_local_tool_returns_errors_to_the_model():
    tool = next(t for t in TXN_TOOLS if t.name == "get_transactions")
    msg = tool.invoke({"type": "tool_call", "id": "1", "name": tool.name,
                       "args": {"legal_entity": "ES", "account_id": "ACC-1001", "as_of": AS_OF}})
    assert "not found in legal entity ES" in msg.content
