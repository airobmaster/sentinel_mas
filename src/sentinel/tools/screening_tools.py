"""Screening tools for the screening agent; they move behind the `screening` MCP server later."""

import re

from langchain_core.tools import tool
from rapidfuzz import fuzz, utils

from sentinel import data
from sentinel.config import settings
from sentinel.tools.txn_tools import render


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


@tool(response_format="content_and_artifact")
def screen_sanctions_pep(name: str, dob: str = "", nationality: str = "") -> tuple[str, list[dict]]:
    """Fuzzy-match a person against the sanctions and PEP lists. Returns each candidate with its
    match score and whether date of birth and nationality agree. A name match alone is not a true match."""
    evidence = []
    for entry in data.watchlist_entries():
        score = fuzz.token_sort_ratio(name, entry["name"], processor=utils.default_process)
        if score < settings.name_match_threshold:
            continue
        detail = entry.get("programme") or entry.get("position", "")
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
        return f"No sanctions or PEP candidates scored {settings.name_match_threshold:.0f}+ for this name.", []
    return render(evidence), evidence


@tool(response_format="content_and_artifact")
def search_adverse_media(name: str) -> tuple[str, list[dict]]:
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
        return "No adverse media found for this name.", []
    return render(evidence), evidence


SCREENING_TOOLS = [screen_sanctions_pep, search_adverse_media]
