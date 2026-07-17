from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from fuglestation.database import DEFAULT_DATABASE_PATH, get_recent_detections
from fuglestation.species_names import format_species_name


CONFIG_PATH = Path("config.toml")
DEFAULT_LIMIT = 10


def load_database_path(path: Path) -> Path:
    """Load the SQLite database path from config.toml."""

    if not path.exists():
        return DEFAULT_DATABASE_PATH

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    database_config = raw_config.get("database", {})
    if not isinstance(database_config, dict):
        raise SystemExit(f"{path} skal indeholde en [database]-sektion.")

    database_path = database_config.get("path", str(DEFAULT_DATABASE_PATH))
    if not isinstance(database_path, str) or not database_path.strip():
        raise SystemExit("database.path skal vaere en database-sti.")

    return Path(database_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vis de seneste BirdNET-detektioner fra SQLite."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Sti til konfigurationsfil. Standard: {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="Sti til SQLite database. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Antal detektioner der skal vises. Standard: {DEFAULT_LIMIT}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit skal vaere mindst 1.")

    database_path = args.database or load_database_path(args.config)
    detections = get_recent_detections(database_path, args.limit)

    if not detections:
        print(f"Ingen detektioner fundet i {database_path}.")
        return

    print(f"Seneste detektioner fra {database_path}:")
    for detection in detections:
        recording_name = Path(detection.recording_path).name
        print(
            f"- {detection.analyzed_at} | {format_species_name(detection.species_name)} "
            f"| {detection.confidence:.3f} "
            f"| {detection.start_time:.1f}-{detection.end_time:.1f} sek. "
            f"| {recording_name}"
        )


if __name__ == "__main__":
    main()
