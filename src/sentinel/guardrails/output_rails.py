"""Output rails for customer-facing text (FR-124, BR-11): no tipping-off and no legal conclusions.
A customer information request must never reveal suspicion, an investigation or a report.

Checked in English and Spanish (the ES legal entity writes to customers in Spanish), and on a copy with
digit-for-letter substitutions undone ("susp1cious"), which the red team (D6-04) showed was needed.
"""

import re

TIPPING_OFF = [
    # Suspicion, investigation, the offences themselves
    r"\bsuspic\w*", r"\binvestigat\w*", r"\bmoney[- ]launder\w*", r"\bAML\b", r"\bSARs?\b",
    r"\bsuspicious activity report\w*", r"\bfinancial crime\b", r"\bfraud\w*", r"\bsanction\w*", r"\bterroris\w*",
    r"\bmule\w*", r"\bstructuring\b", r"\bunder review for\b", r"\bflagged\b", r"\bcompliance (team|investigation)\b",
    r"\bfreez\w* (your )?(account|funds)\b",
    # Reports and the authorities they go to
    r"\breport(ed|ing)? (you |this )?to (the )?(police|authorities|NCA|regulator)",
    r"\b(file|filing|make|making|submit\w*|send\w*)( a| an)? (\w+ )?report\b", r"\breport (about|on) you\b",
    r"\bauthorit(y|ies)\b", r"\bpolice\b", r"\blaw enforcement\b", r"\bregulators?\b",
    r"\bfinancial intelligence\b", r"\bFIU\b", r"\bNCA\b", r"\bSEPBLAC\b",
    # Spanish
    r"\binvestigaci\w*", r"\binvestig\w*", r"\bsospech\w*", r"\bblanqueo\b", r"\bfraude\w*", r"\bautoridad\w*",
    r"\bpolic[ií]a\b", r"\bdenunci\w*", r"\bdelito\w*", r"\bcomunicaci[oó]n de operaciones sospechosas\b",
]
LEGAL_CONCLUSIONS = [r"\bguilty\b", r"\bcriminal\w*\b", r"\billegal\w*\b", r"\bunlawful\w*\b", r"\boffen[cs]e\b",
                     r"\bil[ií]cit\w*\b"]
RAIL = re.compile("|".join(f"(?:{p})" for p in TIPPING_OFF + LEGAL_CONCLUSIONS), re.IGNORECASE)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def check_customer_text(text: str) -> list[str]:
    """Phrases that must not appear in customer-facing text (empty list = passes)."""
    text = text or ""
    hits = [m.group(0) for m in RAIL.finditer(text)]
    hits += [m.group(0) for m in RAIL.finditer(text.translate(LEET))]  # "susp1cious" -> "suspicious"
    return list(dict.fromkeys(hits))
