package sentinel.tools_test

import rego.v1

import data.sentinel.tools

uk_case := {"legal_entity": "UK"}

txn_call(args) := {"agent": "txn", "tool": "get_transactions", "args": args, "case": uk_case}

test_allowed_call if {
	tools.allow with input as txn_call({"legal_entity": "UK", "account_id": "ACC-1", "lookback_days": 90})
}

test_lookback_optional if {
	tools.allow with input as txn_call({"legal_entity": "UK", "account_id": "ACC-1"})
}

test_disposition_tool_denied if {
	d := tools.decision with input as {"agent": "txn", "tool": "close_alert", "args": {"legal_entity": "UK"}, "case": uk_case}
	not d.allow
	"disposition tools are never available to agents" in d.reasons
}

test_tool_outside_agent_list_denied if {
	not tools.allow with input as {"agent": "screening", "tool": "get_transactions", "args": {"legal_entity": "UK"}, "case": uk_case}
}

test_unknown_agent_denied if {
	not tools.allow with input as {"agent": "rogue", "tool": "get_transactions", "args": {"legal_entity": "UK"}, "case": uk_case}
}

test_cross_entity_denied if {
	d := tools.decision with input as txn_call({"legal_entity": "ES", "account_id": "ACC-1"})
	not d.allow
	"legal_entity does not match the case" in d.reasons
}

test_missing_entity_denied if {
	not tools.allow with input as txn_call({"account_id": "ACC-1"})
}

test_lookback_limit if {
	not tools.allow with input as txn_call({"legal_entity": "UK", "account_id": "ACC-1", "lookback_days": 401})
	not tools.allow with input as txn_call({"legal_entity": "UK", "account_id": "ACC-1", "lookback_days": "all"})
}
