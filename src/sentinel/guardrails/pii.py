"""PII redaction with reversible tokens (FR-122, TDD §10).

Models never see PII: names, dates of birth and identifiers are replaced by tokens such as
<PERSON_3fa91c>. Tokens are an HMAC of the value, so the same value always gets the same token and
it cannot be reversed by guessing. Tool calls are de-tokenised just before the tool runs, so the
screening tool still screens the real name. The token -> value map is kept in case state (internal
only) so the console can show investigators the real values.

Detection, in order:
1. email addresses (so a surname inside one does not split it);
2. the case customer's own name, first/middle/surname and date of birth (exact, case-insensitive),
   as typed tokens such as <CUSTOMER_SURNAME_9b1d2e> so agents can still compare name variants;
3. patterns: IBAN, UK sort code, phone number;
4. Presidio (spaCy en_core_web_lg, run as a Docker service) for other people's names.
If the Presidio service is unavailable, 1-3 still run.
"""

import hashlib
import hmac
import logging
import re
from contextvars import ContextVar
from functools import lru_cache

import httpx

from sentinel.config import settings

log = logging.getLogger("sentinel.pii")

PII_VAULT: ContextVar["PiiVault | None"] = ContextVar("sentinel_pii_vault", default=None)

TOKEN = re.compile(r"<[A-Z_]+_[0-9a-f]{6}>")
PATTERNS = {
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b"),
    "SORT_CODE": re.compile(r"\b\d{2}-\d{2}-\d{2}\b"),
    # Letter TLD required, so versioned policy IDs such as policy:AML-UK@3.2#4.6 are not emails
    "EMAIL": re.compile(r"\b[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b"),
    "PHONE": re.compile(r"(?<![\w-])\+?\d[\d ]{8,14}\d\b"),
}
# Spans Presidio may tag as PERSON that are really system identifiers, codes or tokens
# (reason/typology codes such as STRUCTURING_CONFIRMED contain underscores; names never do)
# (and document codes such as TYP-GUIDE or AML-UK, upper case joined by a hyphen)
NOT_A_PERSON = re.compile(r"(CUST|ACC|TXN|CASE|CRM|MED|DEV|OFSI|PEP|TM)-|<|_|policy:|check:|@|\b[A-Z]{2,}-[A-Z]{2,}\b")
# Told to every agent with its brief, so tokens are read as the values they stand for
TOKEN_NOTE = ("\n\nPrivacy: personal data appears as tokens. <CUSTOMER_NAME_x>, <CUSTOMER_FIRST_NAME_x>, "
              "<CUSTOMER_MIDDLE_NAME_x>, <CUSTOMER_SURNAME_x> and <CUSTOMER_DOB_x> are this case's customer; "
              "<PERSON_x> is another person. The same token always means the same value. Compare tokens as you "
              "would the names (e.g. first name + surname without the middle name is still the customer's name).")
NAME_PARTICLES = {"de", "del", "la", "las", "los", "da", "das", "do", "dos", "van", "von", "der", "den", "y"}


def _is_name(span: str) -> bool:
    """Presidio's English model also tags lower-case phrases (e.g. Spanish policy text); names are capitalised."""
    words = re.findall(r"[^\W\d_]+", span)
    return bool(words) and not NOT_A_PERSON.search(span) and all(
        w[0].isupper() for w in words if w.lower() not in NAME_PARTICLES)


def _token(kind: str, value: str) -> str:
    digest = hmac.new(settings.pii_token_key.encode(), value.lower().encode(), hashlib.sha256).hexdigest()
    return f"<{kind}_{digest[:6]}>"


@lru_cache(maxsize=4096)
def _presidio_people(text: str) -> tuple[tuple[int, int], ...]:
    """PERSON spans from the Presidio service; empty if it is unavailable (pattern rules still apply)."""
    if not settings.presidio_url or len(text) < 3:
        return ()
    try:
        resp = httpx.post(f"{settings.presidio_url}/analyze", timeout=10, json={
            "text": text, "language": "en", "entities": ["PERSON"],
            "score_threshold": settings.pii_score_threshold})
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Presidio unavailable (%s); using pattern rules only", type(e).__name__)
        return ()
    return tuple(sorted((r["start"], r["end"]) for r in resp.json()))


def presidio_status() -> tuple[str, bool]:
    """(label, live) for the console header."""
    if not settings.pii_redaction:
        return "off", False
    if not settings.presidio_url:
        return "patterns only", True
    try:
        httpx.get(f"{settings.presidio_url}/health", timeout=3).raise_for_status()
        return "Presidio (lg)", True
    except httpx.HTTPError:
        return "patterns only (Presidio down)", False


class PiiVault:
    """Reversible redaction for one case. `mapping` is token -> original value."""

    def __init__(self, customer: dict | None = None, mapping: dict | None = None):
        self.mapping: dict[str, str] = dict(mapping or {})
        terms: list[tuple[str, str]] = []
        if customer:
            # Typed tokens keep the relation between name parts visible: an article naming "Arlo Voss"
            # becomes <CUSTOMER_FIRST_NAME_..> <CUSTOMER_SURNAME_..>, so the screening agent can still
            # tell it is the customer without the middle name.
            parts = (customer.get("name") or "").split()
            if parts:
                terms.append(("CUSTOMER_NAME", " ".join(parts)))
                kinds = ["CUSTOMER_FIRST_NAME", *["CUSTOMER_MIDDLE_NAME"] * (len(parts) - 2), "CUSTOMER_SURNAME"]
                terms += [(kind, p) for kind, p in zip(kinds, parts) if len(p) >= 3 and len(parts) > 1]
            if customer.get("dob"):
                terms.append(("CUSTOMER_DOB", customer["dob"]))
        # Longest first so "Jordan Ellis" is replaced before "Jordan"; terms is (kind, value)
        self.terms = sorted({t for t in terms if t[1]}, key=lambda t: -len(t[1]))

    def _tok(self, kind: str, value: str) -> str:
        token = _token(kind, value)
        self.mapping[token] = value
        return token

    def redact(self, text: str) -> str:
        if not settings.pii_redaction or not text:
            return text
        # Emails first, so a surname inside an address does not split it
        text = PATTERNS["EMAIL"].sub(lambda m: self._tok("EMAIL", m.group(0)), text)
        for kind, value in self.terms:
            text = re.sub(rf"\b{re.escape(value)}\b",
                          lambda m: self._tok(kind, m.group(0)), text, flags=re.IGNORECASE)
        for kind, pattern in PATTERNS.items():
            if kind != "EMAIL":
                text = pattern.sub(lambda m: self._tok(kind, m.group(0)), text)
        for start, end in reversed(_presidio_people(text)):
            if _is_name(text[start:end]):
                text = text[:start] + self._tok("PERSON", text[start:end]) + text[end:]
        return text

    def mask(self, obj):
        """The reverse of restore: replace every value this vault knows with its token (FR-109, for roles
        that may not see customer data). Covers raw tool evidence, which never went through redact()."""
        if isinstance(obj, str):
            for token, value in sorted(self.mapping.items(), key=lambda kv: -len(kv[1])):
                obj = re.sub(rf"(?<![\w<]){re.escape(value)}(?!\w)", token, obj, flags=re.IGNORECASE)
            return obj
        if isinstance(obj, list):
            return [self.mask(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.mask(v) for k, v in obj.items()}
        return obj

    def restore(self, obj):
        """Swap tokens back to the original values in a string, list or dict (recursively)."""
        if isinstance(obj, str):
            return TOKEN.sub(lambda m: self.mapping.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, list):
            return [self.restore(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.restore(v) for k, v in obj.items()}
        return obj


def redact_content(content, vault: PiiVault):
    """Message content is a string or a list of content blocks."""
    if isinstance(content, str):
        return vault.redact(content)
    if isinstance(content, list):
        return [{**b, "text": vault.redact(b["text"])} if isinstance(b, dict) and isinstance(b.get("text"), str)
                else vault.redact(b) if isinstance(b, str) else b for b in content]
    return content
