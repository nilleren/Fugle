from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


DANISH_NAMES_PATH = Path(__file__).with_name("danish_species_names.json")
MANUAL_DANISH_NAMES_BY_SCIENTIFIC_NAME = {
    "Cyanistes caeruleus": "Blåmejse",
    "Sylvia atricapilla": "Munk",
    "Sylvia borin": "Havesanger",
    "Turdus merula": "Solsort",
}


def split_birdnet_species_name(species_name: str) -> tuple[str, str | None]:
    """Split BirdNET's combined species name into scientific and common names."""

    if "_" not in species_name:
        return species_name, None

    scientific_name, common_name = species_name.split("_", 1)
    return scientific_name, common_name


@lru_cache(maxsize=1)
def load_danish_names() -> dict[str, str]:
    """Load local Danish species names keyed by scientific name."""

    names: dict[str, str] = {}
    if DANISH_NAMES_PATH.exists():
        raw_names = json.loads(DANISH_NAMES_PATH.read_text(encoding="utf-8"))
        if isinstance(raw_names, dict):
            names.update(
                {
                    str(scientific_name): str(danish_name)
                    for scientific_name, danish_name in raw_names.items()
                    if scientific_name and danish_name
                }
            )

    names.update(MANUAL_DANISH_NAMES_BY_SCIENTIFIC_NAME)
    return names


def format_species_name(species_name: str) -> str:
    """Return a UI-friendly species name, preferring Danish plus Latin."""

    scientific_name, common_name = split_birdnet_species_name(species_name)
    danish_name = load_danish_names().get(scientific_name)

    if danish_name:
        return f"{danish_name} / {scientific_name}"
    if common_name:
        return f"{scientific_name} / {common_name}"

    return scientific_name
