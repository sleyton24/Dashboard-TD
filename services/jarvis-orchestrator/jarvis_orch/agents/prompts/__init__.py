"""System prompts versionados. Texto plano (.md) cargado por el seed/runtime."""

from importlib import resources


def load_prompt(name: str) -> str:
    """Lee un prompt empaquetado por nombre (sin extensión).

    Ej: `load_prompt("jarvis_lead")` lee `jarvis_lead.md`.
    """
    return resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")
