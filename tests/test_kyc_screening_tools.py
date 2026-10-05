from sentinel.tools.kyc_tools import get_crm_notes, get_customer_profile
from sentinel.tools.screening_tools import screen_sanctions_pep, search_adverse_media


def call(tool, **args):
    msg = tool.invoke({"type": "tool_call", "id": "1", "name": tool.name, "args": args})
    return msg.content, msg.artifact


def test_profile_has_no_name_or_dob():
    content, evidence = call(get_customer_profile, customer_id="CUST-00042")
    assert evidence[0]["id"] == "cust:CUST-00042"
    assert "Jordan" not in content and "1994" not in content


def test_crm_notes():
    _, evidence = call(get_crm_notes, customer_id="CUST-00077")
    assert [e["id"] for e in evidence] == ["crm:CRM-0077-01"]
    assert call(get_crm_notes, customer_id="CUST-NONE")[1] == []


def test_true_sanctions_match_has_matching_dob():
    content, evidence = call(screen_sanctions_pep, name="Arlo Brennan Voss", dob="1971-04-12", nationality="GB")
    assert [e["id"] for e in evidence] == ["list:OFSI:OFSI-7781"]
    assert "score 100" in content and "matches customer DOB" in content


def test_near_miss_names_are_returned_with_differing_dob():
    _, evidence = call(screen_sanctions_pep, name="Jordan Ellis", dob="1994-02-17", nationality="GB")
    assert [e["id"] for e in evidence] == ["list:OFSI:OFSI-5120"]
    assert "differs from customer DOB" in evidence[0]["summary"]
    _, evidence = call(screen_sanctions_pep, name="Sam Carter", dob="1990-06-14", nationality="GB")
    assert [e["id"] for e in evidence] == ["list:PEP:PEP-221"]


def test_adverse_media_matches_first_and_last_name():
    assert [e["id"] for e in call(search_adverse_media, name="Arlo Brennan Voss")[1]] == ["media:MED-002"]
    assert [e["id"] for e in call(search_adverse_media, name="Jordan Ellis")[1]] == ["media:MED-001"]
    assert call(search_adverse_media, name="Sam Carter")[1] == []
