from __future__ import annotations

import argparse
import csv
import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fuglestation.database import DEFAULT_DATABASE_PATH, save_analysis
from fuglestation.species_names import format_species_name


os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

RECORDINGS_DIR = Path("recordings")
RESULTS_DIR = Path("analysis_results")
CONFIG_PATH = Path("config.toml")
DEFAULT_TOP_K = 5
DEFAULT_CONFIDENCE = 0.1
DEFAULT_GEO_MIN_CONFIDENCE = 0.05


@dataclass(frozen=True)
class BirdNetConfig:
    """BirdNET settings that can be adjusted without changing code."""

    use_geo: bool
    latitude: float | None
    longitude: float | None
    week: int | None
    geo_min_confidence: float


@dataclass(frozen=True)
class Detection:
    """One BirdNET detection from the exported CSV file."""

    start_time: float
    end_time: float
    species_name: str
    confidence: float


def load_birdnet_config(path: Path) -> BirdNetConfig:
    """Load optional BirdNET settings from config.toml."""

    if not path.exists():
        return BirdNetConfig(
            use_geo=False,
            latitude=None,
            longitude=None,
            week=None,
            geo_min_confidence=DEFAULT_GEO_MIN_CONFIDENCE,
        )

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    birdnet_config = raw_config.get("birdnet", {})
    if not isinstance(birdnet_config, dict):
        raise SystemExit(f"{path} skal indeholde en [birdnet]-sektion.")

    use_geo = birdnet_config.get("use_geo", False)
    latitude = birdnet_config.get("latitude")
    longitude = birdnet_config.get("longitude")
    week = birdnet_config.get("week")
    geo_min_confidence = birdnet_config.get(
        "geo_min_confidence", DEFAULT_GEO_MIN_CONFIDENCE
    )

    if not isinstance(use_geo, bool):
        raise SystemExit("birdnet.use_geo skal vaere true eller false.")
    if latitude is not None and not isinstance(latitude, int | float):
        raise SystemExit("birdnet.latitude skal vaere et tal.")
    if longitude is not None and not isinstance(longitude, int | float):
        raise SystemExit("birdnet.longitude skal vaere et tal.")
    if week == 0:
        week = None
    if week is not None and (not isinstance(week, int) or not 1 <= week <= 53):
        raise SystemExit("birdnet.week skal vaere 0 eller et heltal fra 1 til 53.")
    if not isinstance(geo_min_confidence, int | float) or not 0 <= geo_min_confidence <= 1:
        raise SystemExit("birdnet.geo_min_confidence skal vaere mellem 0 og 1.")
    if use_geo and (latitude is None or longitude is None):
        raise SystemExit(
            "birdnet.latitude og birdnet.longitude skal saettes, naar use_geo=true."
        )

    return BirdNetConfig(
        use_geo=use_geo,
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        week=week,
        geo_min_confidence=float(geo_min_confidence),
    )


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


def parse_birdnet_time(value: str) -> float:
    """Parse BirdNET CSV time values as seconds."""

    value = value.strip()
    if ":" not in value:
        return float(value)

    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Uventet tidsformat fra BirdNET: {value}")

    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def find_latest_wav(recordings_dir: Path) -> Path:
    """Return the newest WAV file in recordings_dir."""

    wav_files = sorted(
        recordings_dir.glob("*.wav"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not wav_files:
        raise SystemExit(
            f"Fandt ingen WAV-filer i {recordings_dir}. "
            "Optag en fil foerst med python -m fuglestation.record_audio."
        )
    return wav_files[0]


def build_output_path(audio_path: Path, output_dir: Path) -> Path:
    """Create a CSV path for BirdNET results."""

    return output_dir / f"{audio_path.stem}-birdnet.csv"


def current_iso_week() -> int:
    """Return the current ISO week number."""

    return date.today().isocalendar().week


def read_detections(csv_path: Path) -> list[Detection]:
    """Read BirdNET detections from a CSV file."""

    if not csv_path.exists():
        return []

    detections: list[Detection] = []
    with csv_path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            species_name = row.get("species_name")
            if not species_name:
                continue
            detections.append(
                Detection(
                    start_time=parse_birdnet_time(row["start_time"]),
                    end_time=parse_birdnet_time(row["end_time"]),
                    species_name=species_name,
                    confidence=float(row["confidence"]),
                )
            )
    return detections


def print_summary(detections: list[Detection], output_path: Path) -> None:
    """Print a compact summary for the terminal."""

    print(f"Gemte BirdNET-resultater: {output_path}")
    if not detections:
        print("Ingen detektioner over graensen.")
        return

    print("Detektioner:")
    for detection in detections:
        print(
            f"- {format_species_name(detection.species_name)}: "
            f"{detection.confidence:.3f} "
            f"({detection.start_time:.1f}-{detection.end_time:.1f} sek.)"
        )


def analyze_audio(
    audio_path: Path,
    output_path: Path,
    top_k: int,
    confidence: float,
    birdnet_config: BirdNetConfig,
) -> list[Detection]:
    """Run BirdNET on one WAV file and save CSV results."""

    if not audio_path.exists():
        raise SystemExit(f"Lydfilen findes ikke: {audio_path}")
    if top_k < 1:
        raise SystemExit("--top-k skal vaere mindst 1.")
    if not 0 <= confidence <= 1:
        raise SystemExit("--confidence skal vaere mellem 0 og 1.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    import birdnet

    custom_species_list = None
    if birdnet_config.use_geo:
        week = birdnet_config.week or current_iso_week()
        print(
            "Finder sandsynlige arter for geografi "
            f"({birdnet_config.latitude}, {birdnet_config.longitude}), uge {week}..."
        )
        geo_model = birdnet.load("geo", "2.4", "tf")
        geo_predictions = geo_model.predict(
            birdnet_config.latitude,
            birdnet_config.longitude,
            week=week,
            min_confidence=birdnet_config.geo_min_confidence,
        )
        custom_species_list = sorted(geo_predictions.to_set())
        if not custom_species_list:
            raise SystemExit("BirdNET geo-modellen fandt ingen arter for opsaetningen.")
        print(f"Bruger {len(custom_species_list)} geografisk sandsynlige arter.")

    print(f"Indlaeser BirdNET-model...")
    model = birdnet.load("acoustic", "2.4", "tf")

    print(f"Analyserer {audio_path}...")
    predictions = model.predict(
        audio_path,
        top_k=top_k,
        n_workers=1,
        default_confidence_threshold=confidence,
        custom_species_list=custom_species_list,
        show_stats=None,
    )
    predictions.to_csv(output_path)

    return read_detections(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyser en WAV-fil med BirdNET og gem resultater som CSV."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Sti til konfigurationsfil. Standard: {CONFIG_PATH}.",
    )
    parser.add_argument(
        "audio",
        nargs="?",
        type=Path,
        help="WAV-fil der skal analyseres. Hvis udeladt bruges nyeste WAV i recordings/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Sti til CSV-resultat. Hvis udeladt bruges analysis_results/.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="Sti til SQLite database. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Gem ikke analysen i SQLite.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Antal bedste arter per segment. Standard: {DEFAULT_TOP_K}.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help=f"Minimum confidence for detektioner. Standard: {DEFAULT_CONFIDENCE}.",
    )
    parser.add_argument(
        "--no-geo",
        action="store_true",
        help="Analyser uden geografisk artsliste, selvom config.toml har use_geo=true.",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        help="Breddegrad til BirdNET geo-filter. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        help="Laengdegrad til BirdNET geo-filter. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--week",
        type=int,
        help="ISO-uge til BirdNET geo-filter. Overstyrer config.toml.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    birdnet_config = load_birdnet_config(args.config)
    database_path = args.database or load_database_path(args.config)
    if args.no_geo:
        birdnet_config = BirdNetConfig(
            use_geo=False,
            latitude=birdnet_config.latitude,
            longitude=birdnet_config.longitude,
            week=birdnet_config.week,
            geo_min_confidence=birdnet_config.geo_min_confidence,
        )
    if args.latitude is not None or args.longitude is not None or args.week is not None:
        week = args.week if args.week is not None else birdnet_config.week
        if week == 0:
            week = None
        birdnet_config = BirdNetConfig(
            use_geo=True,
            latitude=(
                args.latitude if args.latitude is not None else birdnet_config.latitude
            ),
            longitude=(
                args.longitude
                if args.longitude is not None
                else birdnet_config.longitude
            ),
            week=week,
            geo_min_confidence=birdnet_config.geo_min_confidence,
        )
        if birdnet_config.latitude is None or birdnet_config.longitude is None:
            raise SystemExit("--latitude og --longitude skal bruges sammen.")

    audio_path = args.audio or find_latest_wav(RECORDINGS_DIR)
    output_path = args.output or build_output_path(audio_path, RESULTS_DIR)

    detections = analyze_audio(
        audio_path=audio_path,
        output_path=output_path,
        top_k=args.top_k,
        confidence=args.confidence,
        birdnet_config=birdnet_config,
    )
    if not args.no_db:
        recording_id = save_analysis(
            database_path=database_path,
            audio_path=audio_path,
            csv_path=output_path,
            detections=detections,
        )
        print(f"Gemte analyse i SQLite: {database_path} (recording_id={recording_id})")
    print_summary(detections, output_path)


if __name__ == "__main__":
    main()
