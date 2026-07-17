from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from fuglestation.analyze_audio import (
    BirdNetConfig,
    DEFAULT_CONFIDENCE,
    DEFAULT_TOP_K,
    build_output_path,
    load_birdnet_config,
    load_database_path,
    print_summary,
    analyze_audio,
)
from fuglestation.database import save_analysis
from fuglestation.record_audio import (
    CONFIG_PATH,
    build_output_path as build_recording_path,
    get_microphones,
    load_config,
    record_audio,
)


@dataclass(frozen=True)
class CycleOptions:
    """Runtime options for one recording and analysis cycle."""

    config_path: Path
    device: int | None
    duration: int | None
    sample_rate: int | None
    confidence: float
    top_k: int
    no_geo: bool
    no_db: bool


def find_configured_microphone(config_path: Path, device_override: int | None):
    """Find the microphone from config.toml or an explicit override."""

    audio_config = load_config(config_path)
    microphones = get_microphones()

    if not microphones:
        raise SystemExit("Kan ikke starte cyklus, fordi ingen mikrofoner blev fundet.")

    device = device_override if device_override is not None else audio_config.device
    if device is None:
        raise SystemExit(
            "run_cycle kraever et mikrofonnummer. "
            "Saet audio.device i config.toml eller brug --device."
        )

    matching_microphones = [
        microphone for microphone in microphones if microphone.index == device
    ]
    if not matching_microphones:
        raise SystemExit(
            f"Mikrofonnummer {device} blev ikke fundet. "
            "Koer python -m fuglestation.record_audio --list for at se muligheder."
        )

    return audio_config, matching_microphones[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optag en lydfil, analyser den med BirdNET og gem i SQLite."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Sti til konfigurationsfil. Standard: {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Mikrofonnummer. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Antal sekunder der skal optages. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        help="Sample rate i Hz. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help=f"Minimum confidence for BirdNET. Standard: {DEFAULT_CONFIDENCE}.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Antal bedste arter per segment. Standard: {DEFAULT_TOP_K}.",
    )
    parser.add_argument(
        "--no-geo",
        action="store_true",
        help="Analyser uden geografisk artsliste, selvom config.toml har use_geo=true.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Gem ikke analysen i SQLite.",
    )
    return parser.parse_args()


def run_single_cycle(options: CycleOptions) -> int:
    """Run one full recording, analysis, and optional SQLite save cycle."""

    audio_config, microphone = find_configured_microphone(
        options.config_path,
        options.device,
    )
    birdnet_config = load_birdnet_config(options.config_path)
    database_path = load_database_path(options.config_path)

    if options.no_geo:
        birdnet_config = BirdNetConfig(
            use_geo=False,
            latitude=birdnet_config.latitude,
            longitude=birdnet_config.longitude,
            week=birdnet_config.week,
            geo_min_confidence=birdnet_config.geo_min_confidence,
        )

    duration_seconds = (
        options.duration if options.duration is not None else audio_config.duration_seconds
    )
    if duration_seconds <= 0:
        raise SystemExit("--duration skal vaere mindst 1 sekund.")

    sample_rate = (
        options.sample_rate or audio_config.sample_rate or microphone.default_samplerate
    )
    if sample_rate <= 0:
        raise SystemExit("--sample-rate skal vaere mindst 1 Hz.")

    audio_path = build_recording_path(audio_config.output_dir)
    csv_path = build_output_path(audio_path, Path("analysis_results"))

    print("Starter fuld fuglestation-cyklus.")
    record_audio(
        microphone=microphone,
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        output_path=audio_path,
    )

    detections = analyze_audio(
        audio_path=audio_path,
        output_path=csv_path,
        top_k=options.top_k,
        confidence=options.confidence,
        birdnet_config=birdnet_config,
    )

    recording_id = 0
    if not options.no_db:
        recording_id = save_analysis(
            database_path=database_path,
            audio_path=audio_path,
            csv_path=csv_path,
            detections=detections,
        )
        print(f"Gemte analyse i SQLite: {database_path} (recording_id={recording_id})")

    print_summary(detections, csv_path)
    print("Cyklus faerdig. Websiden opdaterer automatisk.")
    return recording_id


def main() -> None:
    args = parse_args()
    run_single_cycle(
        CycleOptions(
            config_path=args.config,
            device=args.device,
            duration=args.duration,
            sample_rate=args.sample_rate,
            confidence=args.confidence,
            top_k=args.top_k,
            no_geo=args.no_geo,
            no_db=args.no_db,
        )
    )


if __name__ == "__main__":
    main()
