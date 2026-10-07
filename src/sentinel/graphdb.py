"""Customer network (TDD §3.4).

Customers are linked when they share a device or transfer money between the bank's own accounts.
Each customer gets a connected component, a community (Louvain) and a rule-based mule score.
Two backends answer the same queries:
- Neo4j (+ GDS for components and communities), loaded with `sentinel graph load`;
- an in-memory networkx graph built from the dataset (offline tests, in-process mode).
Queries are fixed templates: no free-form Cypher, at most 2 hops and 50 customers (BR-03).
"""

from collections import defaultdict
from functools import lru_cache
from itertools import combinations

import networkx as nx

from sentinel import data
from sentinel.config import settings

MAX_HOPS = 2
MAX_NODES = 50


def mule_score(peers: int, component_size: int, fan_in: int, forwards_to_customers: bool) -> float:
    """0-1. Shared devices weigh most; then network size, many distinct senders and forwarding."""
    score = (0.35 * min(peers / 4, 1) + 0.25 * min((component_size - 1) / 6, 1)
             + 0.25 * min(fan_in / 12, 1) + 0.15 * forwards_to_customers)
    return round(score, 2)


def build_network() -> tuple[nx.Graph, dict, dict]:
    """(customer graph with `via` edge lists, devices per customer, customer facts) from the dataset."""
    customers = {c["customer_id"]: c for c in data.all_customers()}
    owner = {a: cid for cid, c in customers.items() for a in c["account_ids"]}
    graph = nx.Graph()
    graph.add_nodes_from(customers)
    devices: dict[str, list[dict]] = defaultdict(list)
    users: dict[str, set] = defaultdict(set)
    for link in data.device_links():
        devices[link["customer_id"]].append(link)
        users[link["device_id"]].add(link["customer_id"])
    for device_id, members in users.items():
        for a, b in combinations(sorted(members), 2):
            graph.add_edge(a, b)
            graph.edges[a, b].setdefault("via", []).append(f"device {device_id}")
    forwards = set()
    for t in data.internal_transfers():
        a, b = owner.get(t["account_id"]), owner.get(t["counterparty_account"])
        if a and b and a != b:
            graph.add_edge(a, b)
            graph.edges[a, b].setdefault("via", []).append(f"transfer {t['txn_id']}")
            forwards.add(a)
    senders = data.distinct_senders(settings.graph_as_of, settings.graph_lookback_days)
    facts = {cid: {"legal_entity": c["legal_entity"], "fan_in": max((senders.get(a, 0) for a in c["account_ids"]),
                                                                       default=0),
                   "forwards_to_customers": cid in forwards} for cid, c in customers.items()}
    return graph, devices, facts


def networkx_scores(graph: nx.Graph, devices: dict, facts: dict,
                    components: dict | None = None, communities: dict | None = None) -> dict[str, dict]:
    """Per-customer scores. Components/communities come from GDS when given, else from networkx."""
    if components is None:
        components = {cid: min(comp) for comp in nx.connected_components(graph) for cid in comp}
    if communities is None:
        communities = {cid: min(comm) for comm in nx.community.louvain_communities(graph, seed=42) for cid in comm}
    sizes: dict[str, int] = defaultdict(int)
    for comp in components.values():
        sizes[comp] += 1
    scores = {}
    for cid in graph.nodes:
        peers = len({o for link in devices.get(cid, []) for o in graph.neighbors(cid)
                     if f"device {link['device_id']}" in graph.edges[cid, o]["via"]})
        size = sizes[components[cid]]
        scores[cid] = {
            "component_id": str(components[cid]), "component_size": size, "community_id": str(communities[cid]),
            "shared_device_peers": peers, "fan_in": facts[cid]["fan_in"],
            "forwards_to_customers": facts[cid]["forwards_to_customers"],
            "mule_score": mule_score(peers, size, facts[cid]["fan_in"], facts[cid]["forwards_to_customers"]),
        }
    return scores


class MemoryGraph:
    """networkx graph over the current dataset."""

    def __init__(self):
        self.graph, self.devices, self.facts = build_network()
        self.scores = networkx_scores(self.graph, self.devices, self.facts)
        self.device_users: dict[str, list[str]] = defaultdict(list)
        for cid, links in self.devices.items():
            for link in links:
                self.device_users[link["device_id"]].append(cid)

    def legal_entity(self, customer_id: str) -> str | None:
        return self.facts.get(customer_id, {}).get("legal_entity")

    def customer_scores(self, customer_ids: list[str]) -> dict[str, dict]:
        return {cid: self.scores[cid] for cid in customer_ids if cid in self.scores}

    def links(self, customer_id: str, hops: int) -> list[dict]:
        """Customers within `hops` links, nearest first, with how each first-hop link was made."""
        if customer_id not in self.graph:
            return []
        reached = nx.single_source_shortest_path_length(self.graph, customer_id, cutoff=hops)
        paths = nx.single_source_shortest_path(self.graph, customer_id, cutoff=hops)
        rows = []
        for other, dist in sorted(reached.items(), key=lambda kv: (kv[1], kv[0])):
            if other == customer_id:
                continue
            path = paths[other]
            rows.append({"customer_id": other, "hops": dist, "through": path[1:-1],
                         "via": self.graph.edges[path[-2], other]["via"], **self.scores[other]})
        return rows

    def devices_of(self, customer_id: str) -> list[dict]:
        return [{"device_id": link["device_id"], "device_type": link["device_type"],
                 "users": sorted(self.device_users[link["device_id"]])} for link in self.devices.get(customer_id, [])]


class Neo4jGraph:
    """Same queries against Neo4j (loaded and scored by `sentinel graph load`)."""

    def __init__(self):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))

    def _run(self, cypher: str, **params) -> list[dict]:
        with self.driver.session(database=settings.neo4j_database) as session:
            return session.run(cypher, **params).data()

    def legal_entity(self, customer_id: str) -> str | None:
        rows = self._run("MATCH (c:Customer {customer_id: $cid}) RETURN c.legal_entity AS e", cid=customer_id)
        return rows[0]["e"] if rows else None

    SCORE_FIELDS = ("component_id", "component_size", "community_id", "shared_device_peers", "fan_in",
                    "forwards_to_customers", "mule_score")

    def customer_scores(self, customer_ids: list[str]) -> dict[str, dict]:
        rows = self._run("MATCH (c:Customer) WHERE c.customer_id IN $ids RETURN c", ids=customer_ids)
        return {r["c"]["customer_id"]: {k: r["c"].get(k) for k in self.SCORE_FIELDS} for r in rows}

    def links(self, customer_id: str, hops: int) -> list[dict]:
        hops = int(hops)  # validated 1..2 by the tool; interpolated because Cypher cannot parameterise it
        rows = self._run(
            f"""MATCH p = shortestPath((c:Customer {{customer_id: $cid}})-[:LINKED*1..{hops}]-(o:Customer))
                WHERE o <> c
                RETURN o.customer_id AS customer_id, length(p) AS hops,
                       [n IN nodes(p)[1..-1] | n.customer_id] AS through, last(relationships(p)).via AS via, o
                ORDER BY hops, customer_id LIMIT $limit""",
            cid=customer_id, limit=MAX_NODES,
        )
        return [{"customer_id": r["customer_id"], "hops": r["hops"], "through": r["through"], "via": r["via"],
                 **{k: r["o"].get(k) for k in self.SCORE_FIELDS}} for r in rows]

    def devices_of(self, customer_id: str) -> list[dict]:
        # Users are matched separately: one pattern cannot reuse the customer's own USES relationship,
        # so a single pattern would drop devices that nobody else uses.
        rows = self._run(
            """MATCH (:Customer {customer_id: $cid})-[:USES]->(d:Device)
               MATCH (u:Customer)-[:USES]->(d)
               WITH d, collect(u.customer_id) AS users
               RETURN d.device_id AS device_id, d.device_type AS device_type, users ORDER BY device_id""",
            cid=customer_id,
        )
        return [{**r, "users": sorted(r["users"])} for r in rows]


def _score_colour(score: float | None) -> str:
    if score is None:
        return "#E2E8F0"
    return "#F8B4B4" if score >= 0.6 else "#FCE7B2" if score >= 0.3 else "#E2E8F0"


def network_dot(customer_id: str, max_customers: int = 15) -> str | None:
    """Graphviz DOT for the customer's neighbourhood (2 hops): customers coloured by mule score,
    shared devices as boxes, transfers as dashed lines. None if the customer is not in the graph."""
    net = network()
    own = net.customer_scores([customer_id]).get(customer_id)
    if own is None:
        return None
    links = net.links(customer_id, MAX_HOPS)[:max_customers]
    shown = {customer_id: own, **{l["customer_id"]: l for l in links}}
    lines = ['graph G {', 'graph [rankdir=LR, bgcolor="transparent", fontname="Helvetica"];',
             'node [fontname="Helvetica", fontsize=10, style=filled, color="#94A3B8"];',
             'edge [color="#64748B", fontsize=8, fontname="Helvetica"];']
    for cid, s in shown.items():
        label = f"{cid}\\nmule {s.get('mule_score')}"
        extra = ', penwidth=3, color="#0B2545"' if cid == customer_id else ""
        lines.append(f'"{cid}" [shape=ellipse, label="{label}", fillcolor="{_score_colour(s.get("mule_score"))}"{extra}];')
    devices: dict[tuple[str, str], set] = defaultdict(set)  # (device_id, device_type) -> shown users
    for cid in shown:
        for d in net.devices_of(cid):
            users = [u for u in d["users"] if u in shown]
            if len(d["users"]) > 1 and users:
                devices[(d["device_id"], d["device_type"])].update(users)
    for (device_id, device_type), users in sorted(devices.items()):
        lines.append(f'"{device_id}" [shape=box, label="{device_type}\\n{device_id}", fillcolor="#DBEAFE"];')
        lines += [f'"{u}" -- "{device_id}";' for u in sorted(users)]
    for l in links:
        previous = l["through"][-1] if l["through"] else customer_id
        if any(v.startswith("transfer") for v in l["via"]):
            lines.append(f'"{previous}" -- "{l["customer_id"]}" [style=dashed, label="transfer"];')
    lines.append("}")
    return "\n".join(lines)


def describe_backend() -> tuple[str, bool]:
    """(label, live) for the console: which graph backend answers network queries."""
    net = network()
    if isinstance(net, Neo4jGraph):
        try:
            net.driver.verify_connectivity()
            return "Neo4j + GDS", True
        except Exception:  # noqa: BLE001 - shown as down in the console
            return "Neo4j (down)", False
    return "in-memory", True


@lru_cache
def network():
    """Neo4j when configured and running against Postgres data; otherwise the in-memory graph."""
    if settings.neo4j_uri and settings.data_backend == "postgres":
        return Neo4jGraph()
    return MemoryGraph()


# --- Loading Neo4j ------------------------------------------------------------------------------
def gds_available(session) -> bool:
    try:
        session.run("RETURN gds.version() AS v").single()
        return True
    except Exception:  # noqa: BLE001 - plugin missing or disabled
        return False


def load_graph() -> dict:
    """Rebuild the Neo4j graph from the dataset and write per-customer scores. Uses GDS for
    components (WCC) and communities (Louvain) when the plugin is installed, else networkx."""
    from neo4j import GraphDatabase

    graph, devices, facts = build_network()
    customers = data.all_customers()
    edges = [{"a": a, "b": b, "via": attrs["via"]} for a, b, attrs in graph.edges(data=True)]
    with GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)) as driver:
        with driver.session(database=settings.neo4j_database) as s:
            s.run("MATCH (n) WHERE n:Customer OR n:Account OR n:Device DETACH DELETE n")
            for label, key in (("Customer", "customer_id"), ("Account", "account_id"), ("Device", "device_id")):
                s.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")
            s.run("UNWIND $rows AS r CREATE (:Customer {customer_id: r.customer_id, legal_entity: r.legal_entity, "
                  "segment: r.segment, risk_rating: r.risk_rating})", rows=customers)
            s.run("UNWIND $rows AS r MATCH (c:Customer {customer_id: r.customer_id}) "
                  "UNWIND r.account_ids AS a CREATE (c)-[:OWNS]->(:Account {account_id: a})", rows=customers)
            s.run("UNWIND $rows AS r MATCH (c:Customer {customer_id: r.customer_id}) "
                  "MERGE (d:Device {device_id: r.device_id}) SET d.device_type = r.device_type "
                  "CREATE (c)-[:USES]->(d)", rows=data.device_links())
            s.run("UNWIND $rows AS r MATCH (a:Account {account_id: r.account_id}), "
                  "(b:Account {account_id: r.counterparty_account}) "
                  "CREATE (a)-[:TRANSFERRED {txn_id: r.txn_id, amount: r.amount, date: r.date}]->(b)",
                  rows=data.internal_transfers())
            s.run("UNWIND $rows AS r MATCH (a:Customer {customer_id: r.a}), (b:Customer {customer_id: r.b}) "
                  "CREATE (a)-[:LINKED {via: r.via}]->(b)", rows=edges)

            engine = "networkx"
            components = communities = None
            if gds_available(s):
                engine = "gds"
                drop = "CALL gds.graph.drop('sentinel-customers', false) YIELD graphName RETURN graphName"
                s.run(drop).consume()
                s.run("CALL gds.graph.project('sentinel-customers', 'Customer', {LINKED: {orientation: 'UNDIRECTED'}}) "
                      "YIELD graphName RETURN graphName").consume()
                components = {r["cid"]: r["comp"] for r in s.run(
                    "CALL gds.wcc.stream('sentinel-customers') YIELD nodeId, componentId "
                    "RETURN gds.util.asNode(nodeId).customer_id AS cid, componentId AS comp")}
                communities = {r["cid"]: r["comm"] for r in s.run(
                    "CALL gds.louvain.stream('sentinel-customers') YIELD nodeId, communityId "
                    "RETURN gds.util.asNode(nodeId).customer_id AS cid, communityId AS comm")}
                s.run(drop).consume()
            scores = networkx_scores(graph, devices, facts, components, communities)
            s.run("UNWIND $rows AS r MATCH (c:Customer {customer_id: r.cid}) SET c += r.scores",
                  rows=[{"cid": cid, "scores": sc} for cid, sc in scores.items()])
    network.cache_clear()
    return {"customers": len(customers), "links": len(edges), "devices": len({l["device_id"] for l in data.device_links()}),
            "engine": engine, "max_mule_score": max(sc["mule_score"] for sc in scores.values())}
