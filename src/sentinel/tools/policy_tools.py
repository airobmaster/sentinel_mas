"""Policy tools (`policy_kb` server): search the versioned AML procedures and the typology guide."""

from langchain_core.tools import ToolException

from sentinel.kb import MAX_RESULTS, policy_kb
from sentinel.tools.common import local_tools, nothing_found, opaque

LEGAL_ENTITIES = ("UK", "ES")


def _check_entity(legal_entity: str) -> None:
    if legal_entity not in LEGAL_ENTITIES:
        raise ToolException(f"Unknown legal entity {legal_entity}")


def policy_evidence(chunk: dict, source: str) -> dict:
    return {
        "id": f"policy:{chunk['chunk_id']}",
        "source": source,
        "summary": f"{chunk['title']} v{chunk['policy_version']} §{chunk['section']} {chunk['heading']}: "
                   f"{chunk['text'][:220]}{'…' if len(chunk['text']) > 220 else ''}",
    }


def _render(chunks: list[dict], evidence: list[dict]) -> str:
    return "\n\n".join(f"{e['id']} — {c['title']} v{c['policy_version']} §{c['section']} {c['heading']}\n{c['text']}"
                       for c, e in zip(chunks, evidence))


def search_policy(legal_entity: str, query: str, typology: str = "", k: int = 5) -> tuple[str, list[dict]]:
    """Search the AML procedures for the legal entity and the typology guide. Optionally filter by
    typology code (STRUCT, PASSTHRU, MULE_NETWORK, HRJ, SANCTIONS, PEP, BENIGN). Returns policy
    sections with their evidence IDs (policy:<doc>@<version>#<section>)."""
    _check_entity(legal_entity)
    if not 1 <= k <= MAX_RESULTS:
        raise ToolException(f"k must be between 1 and {MAX_RESULTS}")
    chunks = policy_kb().search(query, legal_entity, typology, k)
    if not chunks:
        return nothing_found("policy_search", opaque(legal_entity, query, typology), "policy_kb.search_policy",
                             f"No policy sections matched the query for {legal_entity}.")
    evidence = [policy_evidence(c, "policy_kb.search_policy") for c in chunks]
    return _render(chunks, evidence), evidence


def get_typology(legal_entity: str, code: str) -> tuple[str, list[dict]]:
    """The typology guide's definition, red flags, mitigating facts and usual disposition for one
    typology code (STRUCT, PASSTHRU, MULE_NETWORK, HRJ, SANCTIONS, PEP, BENIGN)."""
    _check_entity(legal_entity)
    chunk = policy_kb().get_typology(code.upper())
    if not chunk:
        raise ToolException(f"Unknown typology code {code}")
    evidence = [policy_evidence(chunk, "policy_kb.get_typology")]
    return _render([chunk], evidence), evidence


POLICY_FUNCTIONS = [search_policy, get_typology]
POLICY_TOOLS = local_tools(*POLICY_FUNCTIONS)
