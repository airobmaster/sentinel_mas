"""Policy documents, chunking, local search and the policy tools (offline: keyword backend)."""

import pytest
from langchain_core.tools import ToolException

from sentinel.kb import LocalPolicyKB, parse_documents
from sentinel.tools.policy_tools import get_typology, search_policy


def test_documents_are_chunked_by_section_with_versioned_ids():
    chunks = parse_documents()
    ids = {c["chunk_id"] for c in chunks}
    assert {"AML-UK@3.2#4.3", "AML-ES@2.1#3.3", "TYP-GUIDE@1.4#1"} <= ids
    assert {c["legal_entity"] for c in chunks} == {"UK", "ES", "ALL"}
    guide = [c for c in chunks if c["doc_id"] == "TYP-GUIDE"]
    assert sorted(c["typologies"][0] for c in guide) == sorted(
        ["STRUCT", "PASSTHRU", "MULE_NETWORK", "HRJ", "SANCTIONS", "PEP", "BENIGN"])
    assert "STRUCT" in next(c for c in chunks if c["chunk_id"] == "AML-ES@2.1#3.3")["typologies"]  # Spanish tags


def test_local_search_respects_legal_entity_and_typology():
    kb = LocalPolicyKB(parse_documents())
    uk = [c["chunk_id"] for c in kb.search("cash deposits below the reporting threshold branches", "UK")]
    assert uk[:2] == ["TYP-GUIDE@1.4#1", "AML-UK@3.2#4.3"]
    es = kb.search("cash deposits below the reporting threshold", "ES")
    assert all(c["legal_entity"] in ("ES", "ALL") for c in es)
    assert all("MULE_NETWORK" in c["typologies"] for c in kb.search("device", "UK", typology="MULE_NETWORK"))


def test_search_policy_tool_returns_policy_evidence():
    content, evidence = search_policy("UK", "customers sharing the same device", k=3)
    assert evidence and all(e["id"].startswith("policy:") for e in evidence)
    assert "policy:AML-UK@3.2#4.5" in [e["id"] for e in evidence]
    assert "mule score of 0.6" in content


def test_search_policy_limits_and_entity():
    with pytest.raises(ToolException, match="Unknown legal entity"):
        search_policy("FR", "structuring")
    with pytest.raises(ToolException, match="k must be"):
        search_policy("UK", "structuring", k=20)
    _, [nothing] = search_policy("UK", "zzzz qqqq")
    assert nothing["id"].startswith("check:policy_search:")


def test_get_typology():
    content, [evidence] = get_typology("ES", "pep")
    assert evidence["id"] == "policy:TYP-GUIDE@1.4#6" and "PEP_UNEXPLAINED" in content
    with pytest.raises(ToolException, match="Unknown typology"):
        get_typology("UK", "NOPE")
