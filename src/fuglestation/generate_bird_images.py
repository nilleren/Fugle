from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from openai import OpenAI

from fuglestation.ebird import request_json
from fuglestation.species_names import load_danish_names, split_birdnet_species_name


DEFAULT_CANDIDATES_PATH = Path("assets/bird_image_candidates.json")
DEFAULT_OUTPUT_DIR = Path("assets/birds")
DEFAULT_CONFIG_PATH = Path("config.toml")
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


def pose_for_variant(variant: int) -> str:
    """Alternate the default pose so the two generated versions differ."""

    return "i profil" if variant % 2 == 1 else "i flugt"


def pose_instruction_for_variant(variant: int) -> str:
    """Add precise pose constraints for each generated variant."""

    if variant % 2 == 1:
        return (
            "Fuglen skal stå stille i tydelig sideprofil med vingerne "
            "helt foldet ind langs kroppen. Den må ikke flyve, hoppe, lette eller "
            "vises med spredte eller løftede vinger."
        )

    return (
        "Fuglen skal vises i rolig, anatomisk korrekt flugt med vingerne åbne "
        "og hele kroppen synlig."
    )


@dataclass(frozen=True)
class BirdCandidate:
    scientific_name: str
    common_name: str
    danish_name: str | None = None

    @property
    def birdnet_name(self) -> str:
        return f"{self.scientific_name}_{self.common_name}"


def load_env_file(path: Path = Path(".env")) -> dict[str, str]:
    """Load simple KEY=VALUE lines from .env without adding a dependency."""

    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def ensure_openai_key() -> None:
    env_values = load_env_file()
    api_key = os.environ.get(OPENAI_API_KEY_ENV) or env_values.get(OPENAI_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{OPENAI_API_KEY_ENV} mangler. Tilfoej den i .env eller som miljoevariabel."
        )
    os.environ[OPENAI_API_KEY_ENV] = api_key


def slugify_species_name(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return re.sub(r"_+", "_", slug)


def output_path_for(candidate: BirdCandidate, output_dir: Path, variant: int) -> Path:
    suffix = "" if variant == 1 else str(variant)
    filename = f"{slugify_species_name(candidate.birdnet_name)}{suffix}.png"
    return output_dir / filename


def deduplicate_candidates(candidates: list[BirdCandidate]) -> list[BirdCandidate]:
    seen: set[str] = set()
    unique: list[BirdCandidate] = []
    for candidate in candidates:
        key = candidate.scientific_name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def load_candidates_from_file(path: Path) -> list[BirdCandidate]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"{path} skal indeholde en JSON-liste.")

    candidates: list[BirdCandidate] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        scientific_name = str(item.get("scientific_name", "")).strip()
        common_name = str(item.get("common_name", "")).strip()
        danish_name = str(item.get("danish_name", "")).strip() or None
        if scientific_name and common_name:
            candidates.append(BirdCandidate(scientific_name, common_name, danish_name))
    return deduplicate_candidates(candidates)


def load_candidates_from_database(database_path: Path) -> list[BirdCandidate]:
    if not database_path.exists():
        raise RuntimeError(f"Databasen findes ikke: {database_path}")

    danish_names = load_danish_names()
    con = sqlite3.connect(database_path)
    rows = con.execute(
        """
        select species_name, count(*) as n
        from detections
        group by species_name
        order by n desc, species_name
        """
    ).fetchall()

    candidates: list[BirdCandidate] = []
    for species_name, _count in rows:
        scientific_name, common_name = split_birdnet_species_name(str(species_name))
        if common_name:
            candidates.append(
                BirdCandidate(
                    scientific_name=scientific_name,
                    common_name=common_name,
                    danish_name=danish_names.get(scientific_name),
                )
            )
    return candidates


def load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def load_candidates_from_ebird(
    config_path: Path, back_days: int, radius_km: int
) -> list[BirdCandidate]:
    config = load_config(config_path)
    birdnet_config = config.get("birdnet", {})
    if not isinstance(birdnet_config, dict):
        birdnet_config = {}

    latitude = float(birdnet_config.get("latitude", 56.0))
    longitude = float(birdnet_config.get("longitude", 10.0))
    params = {
        "lat": latitude,
        "lng": longitude,
        "dist": radius_km,
        "back": back_days,
        "includeProvisional": "false",
    }
    endpoint = f"/data/obs/geo/recent?{urlencode(params)}"
    observations = request_json(endpoint)
    if not isinstance(observations, list):
        raise RuntimeError("eBird returnerede ikke en liste.")

    danish_names = load_danish_names()
    candidates: list[BirdCandidate] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        scientific_name = str(item.get("sciName", "")).strip()
        common_name = str(item.get("comName", "")).strip()
        if scientific_name and common_name:
            candidates.append(
                BirdCandidate(
                    scientific_name=scientific_name,
                    common_name=common_name,
                    danish_name=danish_names.get(scientific_name),
                )
            )
    return deduplicate_candidates(candidates)


def build_prompt(candidate: BirdCandidate, variant: int, template: str) -> str:
    danish_name = candidate.danish_name or candidate.common_name
    return template.format(
        scientific_name=candidate.scientific_name,
        common_name=candidate.common_name,
        danish_name=danish_name,
        pose=pose_for_variant(variant),
        pose_instruction=pose_instruction_for_variant(variant),
        variant=variant,
    )


def load_prompt_template(path: Path | None) -> str:
    if path:
        return path.read_text(encoding="utf-8").strip()

    return """
Create a naturalistic full-body illustration of {danish_name}, {scientific_name}
({common_name}), suitable for a calm wall display about birds heard in Denmark.

The bird must be the only subject, shown clearly from the side or three-quarter
view, with realistic field-marking colors and proportions. Use soft daylight,
fine feather detail, and a clean warm off-white background. Keep the bird fully
inside the image with generous empty space around it, no branch crossing the
body, no text, no labels, no frame, no watermark. Variant {variant} should have
a slightly different pose from the other version.
""".strip()


def generate_image(
    client: OpenAI,
    model: str,
    prompt: str,
    output_path: Path,
    size: str,
    quality: str,
) -> None:
    result = client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
        quality=quality,
    )
    image_base64 = result.data[0].b64_json
    output_path.write_bytes(base64.b64decode(image_base64))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generer fuglebilleder med OpenAI Image API."
    )
    parser.add_argument(
        "--source",
        choices=["file", "database", "ebird"],
        default="file",
        help="Hvor artslisten skal komme fra. Standard: file.",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="JSON-fil med kandidatarter. Bruges med --source file.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/fuglestation.db"),
        help="SQLite-database. Bruges med --source database.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="config.toml med koordinater. Bruges med --source ebird.",
    )
    parser.add_argument("--ebird-back-days", type=int, default=30)
    parser.add_argument("--ebird-radius-km", type=int, default=25)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--variants", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0, help="0 betyder ingen graense.")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overskriv eksisterende billeder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Vis hvad der ville blive genereret uden at kalde OpenAI.",
    )
    return parser.parse_args()


def load_candidates(args: argparse.Namespace) -> list[BirdCandidate]:
    if args.source == "file":
        return load_candidates_from_file(args.candidates)
    if args.source == "database":
        return load_candidates_from_database(args.database)
    return load_candidates_from_ebird(args.config, args.ebird_back_days, args.ebird_radius_km)


def main() -> None:
    args = parse_args()
    candidates = load_candidates(args)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    prompt_template = load_prompt_template(args.prompt_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        ensure_openai_key()
    client = OpenAI() if not args.dry_run else None

    planned = 0
    generated = 0
    skipped = 0
    for candidate in candidates:
        for variant in range(1, args.variants + 1):
            output_path = output_path_for(candidate, args.output_dir, variant)
            if output_path.exists() and not args.overwrite:
                skipped += 1
                print(f"Springer over, findes allerede: {output_path}")
                continue

            planned += 1
            prompt = build_prompt(candidate, variant, prompt_template)
            if args.dry_run:
                print(f"Ville generere: {output_path}")
                continue

            assert client is not None
            print(f"Genererer: {output_path}")
            generate_image(client, args.model, prompt, output_path, args.size, args.quality)
            generated += 1

    if args.dry_run:
        print(
            f"Dry-run faerdig. {planned} billede(r) ville blive genereret, "
            f"{skipped} sprunget over."
        )
    else:
        print(f"Faerdig. Genererede {generated} billede(r), sprang {skipped} over.")


if __name__ == "__main__":
    main()
