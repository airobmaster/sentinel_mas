"""Policy knowledge base (TDD §4): versioned policy documents, chunked by section, embedded with
Cohere Multilingual v3 on Bedrock and searched with hybrid retrieval (vector + full text, fused by
reciprocal rank) in pgvector. Without Postgres, a local keyword index over the same chunks is used.

Evidence IDs: policy:<doc_id>@<version>#<section>, e.g. policy:AML-UK@3.2#4.3
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from sentinel.config import settings

log = logging.getLogger("sentinel.kb")

# Keywords that tag a procedure section with the typologies it governs (English and Spanish).
TYPOLOGY_KEYWORDS = {
    "STRUCT": ("structuring", "below the reporting threshold", "fraccionamiento", "cash deposits"),
    "PASSTHRU": ("rapid movement", "paid away", "movimiento rápido", "pass-through"),
    "MULE_NETWORK": ("mule", "shared device", "mulas", "dispositivo"),
    "HRJ": ("high-risk", "jurisdiction", "jurisdicción"),
    "SANCTIONS": ("sanctions", "sanciones"),
    "PEP": ("pep", "prp", "politically exposed"),
    "BENIGN": ("explained", "known business pattern", "patrón de negocio"),
}
RRF_K = 60
MAX_RESULTS = 8


def _front_matter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---"):
        head, _, text = text[3:].partition("\n---")
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    return meta, text


def parse_documents(policy_dir: Path | None = None) -> list[dict]:
    """Split each policy document into one chunk per `## <section> <heading>` section."""
    chunks = []
    for path in sorted(Path(policy_dir or settings.policy_dir).glob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
        for block in re.split(r"\n(?=## )", body):
            if not block.startswith("## "):
                continue
            title_line, _, text = block.partition("\n")
            section, _, heading = title_line[3:].strip().partition(" ")
            text = text.strip()
            if meta["doc_id"] == "TYP-GUIDE":
                typologies = [heading.split()[0]]
            else:
                lowered = f"{heading} {text}".lower()
                typologies = [code for code, words in TYPOLOGY_KEYWORDS.items() if any(w in lowered for w in words)]
            chunks.append({
                "chunk_id": f"{meta['doc_id']}@{meta['version']}#{section}",
                "doc_id": meta["doc_id"], "title": meta["title"], "policy_version": meta["version"],
                "legal_entity": meta["legal_entity"], "section": section, "heading": heading,
                "typologies": typologies, "text": text,
            })
    return chunks


# --- Embeddings (Bedrock, Cohere Multilingual v3) -------------------------------------------------
def embed(texts: list[str], input_type: str) -> list[list[float]]:
    """input_type: "search_document" for chunks, "search_query" for queries."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    vectors = []
    for i in range(0, len(texts), 96):  # Cohere accepts up to 96 texts per call
        body = json.dumps({"texts": texts[i:i + 96], "input_type": input_type, "truncate": "END"})
        resp = json.loads(client.invoke_model(modelId=settings.model_embedding, body=body)["body"].read())
        vectors.extend(resp["embeddings"])
    return vectors


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


# --- Backends -------------------------------------------------------------------------------------
class LocalPolicyKB:
    """Keyword search over the parsed chunks; used without Postgres (offline tests, in-process mode)."""

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}

    def search(self, query: str, legal_entity: str, typology: str = "", k: int = 5) -> list[dict]:
        terms = self._tokens(query)
        scored = []
        for c in self.chunks:
            if c["legal_entity"] not in (legal_entity, "ALL") or (typology and typology not in c["typologies"]):
                continue
            score = len(terms & self._tokens(f"{c['heading']} {c['text']}"))
            if score:
                scored.append((score, c))
        return [c for _, c in sorted(scored, key=lambda sc: -sc[0])][:k]

    def get_typology(self, code: str) -> dict | None:
        return next((c for c in self.chunks if c["doc_id"] == "TYP-GUIDE" and code in c["typologies"]), None)


class PgPolicyKB:
    """Hybrid search in kb.policy_chunks: pgvector cosine + full text, fused by reciprocal rank."""

    COLUMNS = "chunk_id, doc_id, title, policy_version, legal_entity, section, heading, typologies, text"

    def _query(self, sql: str, params: tuple) -> list[dict]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(settings.pg_dsn, row_factory=dict_row) as conn:
            return conn.execute(sql, params).fetchall()

    def search(self, query: str, legal_entity: str, typology: str = "", k: int = 5) -> list[dict]:
        try:
            vector = _vector_literal(embed([query], "search_query")[0])
        except Exception as e:  # noqa: BLE001 - degrade to full-text search rather than fail the case
            log.warning("policy search without embeddings (%s): %s", type(e).__name__, e)
            vector = None
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        tsquery = " | ".join(words) or "''"
        typology_filter = "AND %(typology)s = ANY(typologies)" if typology else ""
        vector_cte = (
            "v AS (SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(vec)s::vector) AS r "
            "FROM scope ORDER BY embedding <=> %(vec)s::vector LIMIT 20)"
            if vector else "v AS (SELECT NULL::text AS chunk_id, NULL::bigint AS r WHERE false)"
        )
        sql = f"""
            WITH scope AS (
                SELECT * FROM kb.policy_chunks
                WHERE legal_entity IN (%(entity)s, 'ALL') {typology_filter}),
            {vector_cte},
            t AS (SELECT chunk_id, row_number() OVER (ORDER BY ts_rank(tsv, q) DESC) AS r
                  FROM scope, to_tsquery('simple', %(tsq)s) q WHERE tsv @@ q LIMIT 20)
            SELECT {self.COLUMNS},
                   COALESCE(1.0 / ({RRF_K} + v.r), 0) + COALESCE(1.0 / ({RRF_K} + t.r), 0) AS score
            FROM scope LEFT JOIN v USING (chunk_id) LEFT JOIN t USING (chunk_id)
            WHERE v.r IS NOT NULL OR t.r IS NOT NULL
            ORDER BY score DESC LIMIT %(k)s"""
        return self._query(sql, {"entity": legal_entity, "typology": typology, "vec": vector, "tsq": tsquery, "k": k})

    def get_typology(self, code: str) -> dict | None:
        rows = self._query(f"SELECT {self.COLUMNS} FROM kb.policy_chunks "
                           "WHERE doc_id = 'TYP-GUIDE' AND %s = ANY(typologies)", (code,))
        return rows[0] if rows else None


@lru_cache
def policy_kb():
    if settings.data_backend == "postgres":
        return PgPolicyKB()
    return LocalPolicyKB(parse_documents())


KB_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS kb;
DROP TABLE IF EXISTS kb.policy_chunks;
CREATE TABLE kb.policy_chunks (
    chunk_id       text PRIMARY KEY,
    doc_id         text NOT NULL,
    title          text NOT NULL,
    policy_version text NOT NULL,
    legal_entity   text NOT NULL,
    section        text NOT NULL,
    heading        text NOT NULL,
    typologies     text[] NOT NULL,
    text           text NOT NULL,
    embedding      vector(1024) NOT NULL,
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('simple', heading || ' ' || text)) STORED
);
CREATE INDEX policy_chunks_tsv ON kb.policy_chunks USING gin (tsv);
"""


def load_policies(dsn: str | None = None) -> int:
    """(Re)build kb.policy_chunks from data/policies: parse, embed, insert. Returns the chunk count."""
    import psycopg

    chunks = parse_documents()
    vectors = embed([f"{c['heading']}\n{c['text']}" for c in chunks], "search_document")
    with psycopg.connect(dsn or settings.pg_dsn) as conn:
        conn.execute(KB_SCHEMA_SQL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO kb.policy_chunks (chunk_id, doc_id, title, policy_version, legal_entity, section, "
                "heading, typologies, text, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                [(c["chunk_id"], c["doc_id"], c["title"], c["policy_version"], c["legal_entity"], c["section"],
                  c["heading"], c["typologies"], c["text"], _vector_literal(v)) for c, v in zip(chunks, vectors)],
            )
    return len(chunks)
