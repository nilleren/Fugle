from __future__ import annotations

import argparse
import json
from pathlib import Path

from fuglestation.ebird import request_json
from fuglestation.species_names import DANISH_NAMES_PATH


def fetch_danish_species_names() -> dict[str, str]:
    """Fetch Danish eBird taxonomy names keyed by scientific name."""

    data = request_json(
        "/ref/taxonomy/ebird",
        {"fmt": "json", "locale": "da"},
        timeout_seconds=60,
    )
    if not isinstance(data, list):
        raise RuntimeError("eBird returnerede ikke en taksonomi-liste.")

    names: dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict):
            continue

        scientific_name = row.get("sciName")
        danish_name = row.get("comName")
        if isinstance(scientific_name, str) and isinstance(danish_name, str):
            names[scientific_name] = danish_name

    return dict(sorted(names.items()))


def save_danish_species_names(output_path: Path = DANISH_NAMES_PATH) -> int:
    """Fetch and save Danish species names as a local JSON resource."""

    names = fetch_danish_species_names()
    output_path.write_text(
        json.dumps(names, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(names)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hent danske fuglenavne fra eBird og gem dem lokalt."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DANISH_NAMES_PATH,
        help=f"Sti til outputfil. Standard: {DANISH_NAMES_PATH}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    count = save_danish_species_names(args.output)
    print(f"Gemte {count} danske fuglenavne i {args.output}.")


if __name__ == "__main__":
    main()
