"""Prompt files: first line is `version: x.y`, the rest is the system prompt."""

from pathlib import Path

PROMPT_DIR = Path(__file__).parent


def load_prompt(name: str) -> tuple[str, str]:
    """Return (version, prompt text) for prompts/<name>.md."""
    first, _, body = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"Prompt {name}.md must start with a 'version:' line")
    return first.removeprefix("version:").strip(), body.strip()
