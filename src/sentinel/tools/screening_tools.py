"""Screening tools (`screening` server). Name-based, so not scoped to a customer record."""

import re

from rapidfuzz import fuzz, utils

from sentinel import data
from sentinel.config import settings
from sentinel.tools.common import local_tools, nothing_found, opaque, render


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def screen_sanctions_pep(legal_entity: str, name: str, dob: str = "", nationality: str = "") -> tuple[str, list[dict]]:
    """Fuzzy-match a person against the sanctions and PEP lists. Returns each candidate with its
    match score and whether date of birth and nationality agree. A name match alone is not a true match."""
    evidence = []
    for entry in data.watchlist_entries():
        score = fuzz.token_sort_ratio(name, entry["name"], processor=utils.default_process)
        if score < settings.name_match_threshold:
            continue
        detail = entry.get("programme") or entry.get("position") or ""
        evidence.append(
            {
                "id": f"list:{entry['list']}:{entry['entry_id']}",
                "source": "screening.screen_sanctions_pep",
                "summary": (
                    f"{entry['list']} entry '{entry['name']}' ({detail}), name score {score:.0f}, "
                    f"listed DOB {entry['dob']} ({'matches' if dob and dob == entry['dob'] else 'differs from'} "
                    f"customer DOB {dob or 'unknown'}), listed nationality {entry['nationality']} "
                    f"({'matches' if nationality == entry['nationality'] else 'differs'})"
                ),
            }
        )
    if not evidence:
        return nothing_found(
            "sanctions_pep", opaque(name, dob), "screening.screen_sanctions_pep",
            f"Sanctions and PEP screening: no candidates scored {settings.name_match_threshold:.0f}+ for the customer's name.",
        )
    return render(evidence), evidence


def search_adverse_media(legal_entity: str, name: str) -> tuple[str, list[dict]]:
    """Find news articles that mention a person's first name and surname. Articles may be about a
    different person with the same name: check age, location and context."""
    parts = name.lower().split()
    wanted = {parts[0], parts[-1]}
    evidence = [
        {
            "id": f"media:{a['article_id']}",
            "source": "screening.search_adverse_media",
            "summary": f"{a['date']} '{a['headline']}': {a['text']}",
        }
        for a in data.adverse_media()
        if wanted <= _tokens(f"{a['headline']} {a['text']}")
    ]
    if not evidence:
        return nothing_found("adverse_media", opaque(name), "screening.search_adverse_media",
                             "Adverse media search: no articles mention the customer's name.")
    return render(evidence), evidence


SCREENING_FUNCTIONS = [screen_sanctions_pep, search_adverse_media]
SCREENING_TOOLS = local_tools(*SCREENING_FUNCTIONS)
