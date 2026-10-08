"""Output rails for customer-facing text (FR-124, BR-11): no tipping-off and no legal conclusions.
A customer information request must never reveal suspicion, an investigation or a report."""

import re

TIPPING_OFF = [
    r"\bsuspic\w*", r"\binvestigat\w*", r"\bmoney[- ]launder\w*", r"\bAML\b", r"\bSARs?\b",
    r"\bsuspicious activity report\w*", r"\breport(ed|ing)? (you |this )?to (the )?(police|authorities|NCA|regulator)",
    r"\bfinancial crime\b", r"\bfraud\w*", r"\bsanction\w*", r"\bterroris\w*", r"\bmule\w*",
    r"\bstructuring\b", r"\bunder review for\b", r"\bflagged\b", r"\bcompliance (team|investigation)\b",
    r"\bfreez\w* (your )?(account|funds)\b",
]
LEGAL_CONCLUSIONS = [r"\bguilty\b", r"\bcriminal\w*\b", r"\billegal\w*\b", r"\bunlawful\w*\b", r"\boffen[cs]e\b"]
RAIL = re.compile("|".join(f"(?:{p})" for p in TIPPING_OFF + LEGAL_CONCLUSIONS), re.IGNORECASE)


def check_customer_text(text: str) -> list[str]:
    """Phrases that must not appear in customer-facing text (empty list = passes)."""
    return list(dict.fromkeys(m.group(0) for m in RAIL.finditer(text or "")))
