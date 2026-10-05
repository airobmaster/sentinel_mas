"""The generator is deterministic and the planted typologies are detectable by the tools."""

import json

import pytest

from sentinel import data
from sentinel.config import settings
from sentinel.datagen.generator import TYPOLOGIES, generate
from sentinel.tools.screening_tools import screen_sanctions_pep
from sentinel.tools.txn_tools import detect_pass_through, detect_structuring

AS_OF = "2026-03-31"


@pytest.fixture(scope="module")
def dataset():
    return generate(42)


@pytest.fixture
def generated_backend(dataset, tmp_path, monkeypatch):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    monkeypatch.setattr(settings, "dataset_paths", [path])
    data.backend.cache_clear()
    return dataset


def cases_of(dataset: dict, typology: str) -> list[dict]:
    return [a for a in dataset["alerts"] if dataset["ground_truth"][a["case_id"]]["typology"] == typology]


def test_deterministic_for_a_seed(dataset):
    assert json.dumps(generate(42), sort_keys=True) == json.dumps(dataset, sort_keys=True)
    assert json.dumps(generate(7), sort_keys=True) != json.dumps(dataset, sort_keys=True)


def test_every_alert_has_ground_truth_and_valid_references(dataset):
    customers = {c["customer_id"]: c for c in dataset["customers"]}
    txns = {t["txn_id"]: t for t in dataset["transactions"]}
    assert len(dataset["alerts"]) == sum(n for n, _, _ in TYPOLOGIES.values())
    for alert in dataset["alerts"]:
        assert alert["case_id"] in dataset["ground_truth"]
        customer = customers[alert["customer_id"]]
        assert alert["account_ids"] == customer["account_ids"]
        assert alert["legal_entity"] == customer["legal_entity"]
        assert all(txns[t]["account_id"] == alert["account_ids"][0] for t in alert["triggering_txn_ids"])
        assert "expected" not in alert  # ground truth is kept out of the alert payload


def test_structuring_is_detected_for_struct_and_cash_business(generated_backend):
    for typology in ("STRUCT", "BENIGN_CASH_BUSINESS"):  # same pattern; only the profile explains the benign one
        for alert in cases_of(generated_backend, typology):
            content, _ = detect_structuring(alert["legal_entity"], alert["account_ids"][0], AS_OF)
            assert "STRUCTURING PATTERN" in content, (typology, alert["case_id"])


def test_pass_through_is_detected(generated_backend):
    for alert in cases_of(generated_backend, "PASSTHRU"):
        content, _ = detect_pass_through(alert["legal_entity"], alert["account_ids"][0], AS_OF)
        assert "PASS-THROUGH PATTERN" in content, alert["case_id"]


def test_sanctions_true_and_near_matches(generated_backend):
    customers = {c["customer_id"]: c for c in generated_backend["customers"]}
    for typology, dob_agrees in (("SANCT_TRUE", True), ("SANCT_NEAR", False)):
        for alert in cases_of(generated_backend, typology):
            c = customers[alert["customer_id"]]
            _, evidence = screen_sanctions_pep(c["legal_entity"], c["name"], c["dob"], c["nationality"])
            summaries = [e["summary"] for e in evidence if e["id"].startswith("list:OFSI:")]
            assert summaries, alert["case_id"]
            assert any(("matches customer DOB" in s) == dob_agrees for s in summaries), alert["case_id"]
