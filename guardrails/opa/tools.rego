# Tool-call authorisation for Sentinel agents (TDD §9). Deny by default.
#
# Input:  {"agent": "txn", "tool": "get_transactions", "args": {...}, "case": {"legal_entity": "UK"}}
# Output: data.sentinel.tools.decision = {"allow": bool, "reasons": [...]}
package sentinel.tools

import rego.v1

# Disposition and data-changing actions are never available to any agent.
forbidden := {"close_alert", "escalate_alert", "file_sar", "update_customer"}

max_lookback_days := 400

default allow := false

allow if count(deny_reasons) == 0

deny_reasons contains "disposition tools are never available to agents" if input.tool in forbidden

deny_reasons contains sprintf("tool %s is not allowed for agent %s", [input.tool, input.agent]) if {
	not input.tool in object.get(data.agent_tools, input.agent, [])
}

deny_reasons contains "legal_entity is required" if not input.args.legal_entity

deny_reasons contains "legal_entity does not match the case" if {
	input.args.legal_entity
	input.args.legal_entity != input.case.legal_entity
}

deny_reasons contains sprintf("lookback_days must be 1-%d", [max_lookback_days]) if {
	some days in [input.args.lookback_days]
	not valid_lookback(days)
}

valid_lookback(days) if {
	is_number(days)
	days >= 1
	days <= max_lookback_days
}

decision := {"allow": allow, "reasons": deny_reasons}
