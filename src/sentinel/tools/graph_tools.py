"""Network tools (`graph_query` server): fixed graph templates only, at most 2 hops and 50 customers
(BR-03). Related customers outside the case's legal entity are never returned."""

from langchain_core.tools import ToolException

from sentinel.graphdb import MAX_HOPS, MAX_NODES, network
from sentinel.tools.common import check_customer, local_tools, nothing_found, render


def describe_scores(s: dict) -> str:
    return (f"mule score {s['mule_score']}, network of {s['component_size']} customer(s), community "
            f"{s['community_id']}, {s['shared_device_peers']} shared-device peer(s), {s['fan_in']} distinct "
            f"sender(s), {'forwards money to other customers' if s['forwards_to_customers'] else 'no forwarding'}")


def _self_evidence(customer_id: str) -> dict:
    scores = network().customer_scores([customer_id]).get(customer_id)
    if not scores:
        raise ToolException(f"Customer {customer_id} is not in the network graph")
    return {"id": f"graph:{customer_id}", "source": "graph_query.get_neighbourhood",
            "summary": f"{customer_id} network profile: {describe_scores(scores)}"}


def get_neighbourhood(legal_entity: str, customer_id: str, hops: int = 2, max_nodes: int = 20) -> tuple[str, list[dict]]:
    """Customers linked to this customer through shared devices or transfers between the bank's own
    accounts, up to `hops` (1-2) links away, with each one's mule score. Also returns the customer's
    own network profile."""
    check_customer(legal_entity, customer_id)
    if not 1 <= hops <= MAX_HOPS or not 1 <= max_nodes <= MAX_NODES:
        raise ToolException(f"hops must be 1-{MAX_HOPS} and max_nodes 1-{MAX_NODES}")
    evidence = [_self_evidence(customer_id)]
    links = [l for l in network().links(customer_id, hops)
             if network().legal_entity(l["customer_id"]) == legal_entity][:max_nodes]
    if not links:
        content, empty = nothing_found("network_links", customer_id, "graph_query.get_neighbourhood",
                                       f"{customer_id} has no links to other customers within {hops} hop(s).")
        return f"{render(evidence)}\n{content}", evidence + empty
    for l in links:
        route = f" through {', '.join(l['through'])}" if l["through"] else ""
        evidence.append({"id": f"graph:{l['customer_id']}", "source": "graph_query.get_neighbourhood",
                         "summary": f"{l['customer_id']} is {l['hops']} hop(s) from {customer_id}{route}, linked by "
                                    f"{', '.join(l['via'])}; {describe_scores(l)}"})
    return render(evidence), evidence


def get_shared_devices(legal_entity: str, customer_id: str) -> tuple[str, list[dict]]:
    """Devices this customer uses and which other customers use the same device."""
    check_customer(legal_entity, customer_id)
    devices = network().devices_of(customer_id)
    if not devices:
        return nothing_found("devices", customer_id, "graph_query.get_shared_devices",
                             f"No devices recorded for {customer_id}.")
    evidence = []
    for d in devices:
        others = [u for u in d["users"] if u != customer_id and network().legal_entity(u) == legal_entity]
        shared = f"shared with {len(others)} other customer(s): {', '.join(others)}" if others else "not shared"
        evidence.append({"id": f"graph:device:{d['device_id']}", "source": "graph_query.get_shared_devices",
                         "summary": f"{d['device_type']} {d['device_id']} used by {customer_id}, {shared}"})
    return render(evidence), evidence


def get_community_scores(legal_entity: str, customer_ids: list[str]) -> tuple[str, list[dict]]:
    """Network profile (community, network size, mule score) for up to 50 customers."""
    if not 1 <= len(customer_ids) <= MAX_NODES:
        raise ToolException(f"Give 1-{MAX_NODES} customer IDs")
    scores = network().customer_scores(customer_ids)
    evidence = [{"id": f"graph:{cid}", "source": "graph_query.get_community_scores",
                 "summary": f"{cid} network profile: {describe_scores(s)}"}
                for cid, s in sorted(scores.items()) if network().legal_entity(cid) == legal_entity]
    if not evidence:
        raise ToolException(f"None of these customers are in legal entity {legal_entity}")
    return render(evidence), evidence


GRAPH_FUNCTIONS = [get_neighbourhood, get_shared_devices, get_community_scores]
GRAPH_TOOLS = local_tools(*GRAPH_FUNCTIONS)
