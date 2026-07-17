from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import birdnet

from fuglestation.analyze_audio import load_birdnet_config
from fuglestation.species_names import load_danish_names, split_birdnet_species_name


DEFAULT_OUTPUT_PATH = Path("data/birdnet_year_round_candidates.json")
DEFAULT_MISSING_OUTPUT_PATH = Path("data/birdnet_year_round_missing_images.json")
DEFAULT_IMAGE_DIR = Path("assets/birds")
DEFAULT_CONFIG_PATH = Path("config.toml")
BIRDNET_GEO_WEEKS = range(1, 49)


def image_stem_for(scientific_name: str, common_name: str | None) -> str:
    """Return the image filename stem used by the wall display."""

    birdnet_name = scientific_name if common_name is None else f"{scientific_name}_{common_name}"
    stem = re.sub(r"[^A-Za-z0-9]+", "_", birdnet_name).strip("_")
    return re.sub(r"_+", "_", stem)


def image_variants_for(stem: str, image_stems: set[str]) -> list[str]:
    """Find all available image variants for one BirdNET species stem."""

    variant_pattern = re.compile(re.escape(stem) + r"\d+")
    return sorted(
        image_stem
        for image_stem in image_stems
        if image_stem == stem or variant_pattern.fullmatch(image_stem)
    )


def build_year_round_species(
    latitude: float,
    longitude: float,
    min_confidence: float,
) -> dict[str, set[int]]:
    """Ask BirdNET's geo model for every ISO week and return species -> weeks."""

    geo_model = birdnet.load("geo", "2.4", "tf")
    species_weeks: dict[str, set[int]] = {}

    for week in BIRDNET_GEO_WEEKS:
        predictions = geo_model.predict(
            latitude,
            longitude,
            week=week,
            min_confidence=min_confidence,
        )
        for species_name in predictions.to_set():
            species_weeks.setdefault(species_name, set()).add(week)
        print(
            f"Uge {week:02d}: {len(predictions.to_set())} arter, "
            f"{len(species_weeks)} unikke i alt."
        )

    return species_weeks


def species_payload(
    species_name: str,
    weeks: set[int],
    danish_names: dict[str, str],
    image_stems: set[str],
) -> dict[str, object]:
    """Return a JSON-friendly species record."""

    scientific_name, common_name = split_birdnet_species_name(species_name)
    stem = image_stem_for(scientific_name, common_name)
    variants = image_variants_for(stem, image_stems)

    return {
        "scientific_name": scientific_name,
        "common_name": common_name,
        "danish_name": danish_names.get(scientific_name),
        "birdnet_name": species_name,
        "weeks": sorted(weeks),
        "image_stem": stem,
        "image_variants": variants,
        "has_image": bool(variants),
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Byg en helars BirdNET-kandidatliste for den lokale geografi og "
            "sammenlign med fuglebillederne."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Sti til config.toml. Standard: config.toml.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"JSON-fil til alle helarsarter. Standard: {DEFAULT_OUTPUT_PATH}.",
    )
    parser.add_argument(
        "--missing-output",
        type=Path,
        default=DEFAULT_MISSING_OUTPUT_PATH,
        help=(
            "JSON-fil til arter der mangler billeder. "
            f"Standard: {DEFAULT_MISSING_OUTPUT_PATH}."
        ),
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=DEFAULT_IMAGE_DIR,
        help=f"Mappe med fuglebilleder. Standard: {DEFAULT_IMAGE_DIR}.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Overstyr birdnet.geo_min_confidence fra config.toml.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_birdnet_config(args.config)
    if config.latitude is None or config.longitude is None:
        raise SystemExit("birdnet.latitude og birdnet.longitude skal vaere sat.")

    min_confidence = (
        config.geo_min_confidence
        if args.min_confidence is None
        else args.min_confidence
    )
    image_stems = {path.stem for path in args.image_dir.glob("*.png")}
    danish_names = load_danish_names()

    species_weeks = build_year_round_species(
        config.latitude,
        config.longitude,
        min_confidence,
    )
    records = [
        species_payload(species_name, weeks, danish_names, image_stems)
        for species_name, weeks in sorted(species_weeks.items())
    ]
    missing_records = [record for record in records if not record["has_image"]]

    write_json(args.output, records)
    write_json(args.missing_output, missing_records)

    print("")
    print(f"Helarsarter: {len(records)}")
    print(f"Arter med billede: {len(records) - len(missing_records)}")
    print(f"Arter der mangler billede: {len(missing_records)}")
    print(f"Skrev helarsliste: {args.output}")
    print(f"Skrev mangelliste: {args.missing_output}")

    if missing_records:
        print("")
        print("Mangler billeder:")
        for record in missing_records:
            danish_name = record.get("danish_name") or "-"
            print(
                f"- {danish_name}: {record['scientific_name']} / "
                f"{record['common_name']}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
