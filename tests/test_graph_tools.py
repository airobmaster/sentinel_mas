"""Customer network (in-memory backend) and the network tools, on the generated dataset."""

import json

import pytest
from langchain_core.tools import ToolException

from sentinel import data, graphdb
from sentinel.config import REPO_ROOT, settings
from sentinel.datagen.generator import generate
from sentinel.graphdb import mule_score
from sentinel.tools.graph_tools import get_community_scores, get_neighbourhood, get_shared_devices


@pytest.fixture(scope="module")
def dataset():
    return generate(42)


@pytest.fixture
def generated(dataset, tmp_path, monkeypatch):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    monkeypatch.setattr(settings, "dataset_paths", [REPO_ROOT / "data" / "fixtures" / "cases.json", path])
    data.backend.cache_clear()
    graphdb.network.cache_clear()
    return dataset


def alerts_of(dataset, typology):
    return [a for a in dataset["alerts"] if dataset["ground_truth"][a["case_id"]]["typology"] == typology]


def test_mule_score_formula():
    assert mule_score(0, 1, 0, False) == 0.0
    assert mule_score(4, 7, 12, True) == 1.0
    assert mule_score(1, 2, 1, False) < 0.2  # a household sharing one tablet


def test_mule_rings_are_found_and_score_high(generated):
    for alert in alerts_of(generated, "MULE_RING"):
        _, evidence = get_neighbourhood("UK", alert["customer_id"])
        own = evidence[0]
        assert own["id"] == f"graph:{alert['customer_id']}" and "mule score 0.8" in own["summary"]
        linked = [e for e in evidence[1:] if e["id"].startswith("graph:CUST")]
        assert len(linked) >= 4 and all("linked by device DEV-R" in e["summary"] for e in linked[:3])
        _, devices = get_shared_devices("UK", alert["customer_id"])
        assert any("shared with" in d["summary"] for d in devices)


def test_household_device_sharing_scores_low(generated):
    net = graphdb.network()
    households = [cid for cid, s in net.scores.items() if s["shared_device_peers"] == 1]
    assert households and all(net.scores[c]["mule_score"] < 0.6 for c in households)


def test_isolated_customer_returns_a_citable_negative(generated):
    _, evidence = get_neighbourhood("UK", "CUST-00042")  # fixture customer: no devices, no transfers
    assert [e["id"] for e in evidence] == ["graph:CUST-00042", "check:network_links:CUST-00042"]


def test_limits_and_entity_scoping(generated):
    with pytest.raises(ToolException, match="hops"):
        get_neighbourhood("UK", "CUST-00042", hops=3)
    with pytest.raises(ToolException, match="not found in legal entity ES"):
        get_neighbourhood("ES", "CUST-00042")
    with pytest.raises(ToolException, match="1-50"):
        get_community_scores("UK", [f"C{i}" for i in range(51)])
    ring = alerts_of(generated, "MULE_RING")[0]["customer_id"]
    _, evidence = get_community_scores("UK", [ring, "CUST-00042"])
    assert [e["id"] for e in evidence] == [f"graph:{ring}", "graph:CUST-00042"][::1 if ring < "CUST-00042" else -1]
