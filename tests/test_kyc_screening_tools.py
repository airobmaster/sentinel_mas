import pytest
from langchain_core.tools import ToolException

from sentinel.tools.case_tools import get_case_history
from sentinel.tools.kyc_tools import get_crm_notes, get_customer_profile
from sentinel.tools.screening_tools import screen_sanctions_pep, search_adverse_media


def test_profile_has_no_name_or_dob():
    content, evidence = get_customer_profile("UK", "CUST-00042")
    assert evidence[0]["id"] == "cust:CUST-00042"
    assert "Jordan" not in content and "1994" not in content


def test_customer_must_belong_to_legal_entity():
    with pytest.raises(ToolException, match="not found in legal entity ES"):
        get_customer_profile("ES", "CUST-00042")
    with pytest.raises(ToolException):
        get_crm_notes("UK", "CUST-NONE")


def test_crm_notes():
    _, evidence = get_crm_notes("UK", "CUST-00077")
    assert [e["id"] for e in evidence] == ["crm:CRM-0077-01"]


def test_case_history_empty_for_fixtures():
    content, evidence = get_case_history("UK", "CUST-00042")
    assert [e["id"] for e in evidence] == ["check:case_history:CUST-00042"] and "No prior cases" in content


def test_true_sanctions_match_has_matching_dob():
    content, evidence = screen_sanctions_pep("UK", "Arlo Brennan Voss", "1971-04-12", "GB")
    assert [e["id"] for e in evidence] == ["list:OFSI:OFSI-7781"]
    assert "score 100" in content and "matches customer DOB" in content


def test_near_miss_names_are_returned_with_differing_dob():
    _, evidence = screen_sanctions_pep("UK", "Jordan Ellis", "1994-02-17", "GB")
    assert [e["id"] for e in evidence] == ["list:OFSI:OFSI-5120"]
    assert "differs from customer DOB" in evidence[0]["summary"]
    _, evidence = screen_sanctions_pep("UK", "Sam Carter", "1990-06-14", "GB")
    assert [e["id"] for e in evidence] == ["list:PEP:PEP-221"]


def test_adverse_media_matches_first_and_last_name():
    assert [e["id"] for e in search_adverse_media("UK", "Arlo Brennan Voss")[1]] == ["media:MED-002"]
    assert [e["id"] for e in search_adverse_media("UK", "Jordan Ellis")[1]] == ["media:MED-001"]
    [clean] = search_adverse_media("UK", "Sam Carter")[1]
    assert clean["id"].startswith("check:adverse_media:") and "Sam" not in clean["id"]  # no PII in IDs


def test_clean_screening_is_citable_without_pii():
    content, [evidence] = screen_sanctions_pep("UK", "Nobody Matches", "1980-01-01", "GB")
    assert evidence["id"].startswith("check:sanctions_pep:") and "Nobody" not in evidence["id"] + content
