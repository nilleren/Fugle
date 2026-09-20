from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import choice
from re import fullmatch
from re import sub
from shutil import copyfile

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fuglestation.analyze_audio import DEFAULT_CONFIDENCE
from fuglestation.audio_clips import DEFAULT_SPECIES_CLIPS_DIR
from fuglestation.audio_clips import load_species_clip_index
from fuglestation.audio_clips import save_species_clip_index
from fuglestation.database import (
    DEFAULT_DATABASE_PATH,
    delete_species_clip_detection,
    get_detection_overview,
    get_recent_detections,
    get_species_summary,
    get_species_statistics,
    get_species_statistics_for_name,
)
from fuglestation.eink_image import render_eink_wall_image
from fuglestation.record_audio import build_output_path
from fuglestation.record_audio import Microphone
from fuglestation.record_audio import find_preferred_microphone
from fuglestation.record_audio import load_config as load_audio_config
from fuglestation.record_audio import get_microphones, record_audio
from fuglestation.species_names import format_species_name
from fuglestation.station_status import DEFAULT_STATUS_PATH, read_status
from fuglestation.station_status import StationStatus, now_iso, write_status


CONFIG_PATH = Path("config.toml")
DEFAULT_CONFIG_PATH = Path("config.default.toml")
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = Path("assets")
BIRD_ASSETS_DIR = ASSETS_DIR / "birds"
SCHEDULER_LOG_PATH = Path("data/scheduler.log")
TEST_RECORDING_SECONDS = 3
DEFAULT_WALL_MAX_SPECIES = 18
DEFAULT_WALL_RECENT_MINUTES = 180
DEFAULT_WALL_SHOW_NAMES = True
DEFAULT_WALL_SHOW_LATIN_NAMES = True
DEFAULT_WALL_SHOW_FOOTER = True
DEFAULT_WALL_SHOW_SHADOWS = False
DEFAULT_WALL_SIZE_MODE = "common"
DEFAULT_WALL_MIN_CONFIDENCE = 0.5
DEFAULT_WALL_EINK_BACKGROUND = "#FBF2D6"
DEFAULT_SITE_TITLE = "Fuglene i haven"
WALL_SIZE_MODES = {"equal", "common", "rare"}
scheduler_process: subprocess.Popen | None = None


class AudioDeviceUpdate(BaseModel):
    """Request body for choosing an audio input device."""

    device: int


class RuntimeSettingsUpdate(BaseModel):
    """Request body for core station runtime settings."""

    site_title: str
    duration_seconds: int
    recordings_to_keep: int
    birdnet_min_confidence: float
    wall_min_confidence: float
    quiet_start: str
    quiet_end: str
    wall_max_species: int
    wall_recent_minutes: int
    wall_show_names: bool
    wall_show_latin_names: bool
    wall_show_footer: bool
    wall_show_shadows: bool
    wall_size_mode: str
    wall_eink_background: str = DEFAULT_WALL_EINK_BACKGROUND


def load_database_path(path: Path) -> Path:
    """Load the SQLite database path from config.toml."""

    if not path.exists():
        return DEFAULT_DATABASE_PATH

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    database_config = raw_config.get("database", {})
    if not isinstance(database_config, dict):
        return DEFAULT_DATABASE_PATH

    database_path = database_config.get("path", str(DEFAULT_DATABASE_PATH))
    if not isinstance(database_path, str) or not database_path.strip():
        return DEFAULT_DATABASE_PATH

    return Path(database_path)


def load_recordings_dir(path: Path) -> Path:
    """Load the audio recordings directory from config.toml."""

    if not path.exists():
        return Path("recordings")

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    audio_config = raw_config.get("audio", {})
    if not isinstance(audio_config, dict):
        return Path("recordings")

    output_dir = audio_config.get("output_dir", "recordings")
    if not isinstance(output_dir, str) or not output_dir.strip():
        return Path("recordings")

    return Path(output_dir)


def load_config(path: Path) -> dict[str, object]:
    """Load the project config for display in the web UI."""

    if not path.exists():
        return {}

    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def bird_image_stem(species_name: str) -> str:
    """Return the expected local image filename stem for a BirdNET species."""

    cleaned = sub(r"[^A-Za-z0-9]+", "_", species_name).strip("_")
    return cleaned or "unknown_species"


def find_bird_image_candidates(species_name: str) -> list[Path]:
    """Return local bird image paths for a species, including numbered variants."""

    stem = bird_image_stem(species_name)
    image_paths: list[Path] = []
    for extension in ("png", "jpg", "jpeg", "webp"):
        image_paths.extend(sorted(BIRD_ASSETS_DIR.glob(f"{stem}*.{extension}")))

    return [
        image_path
        for image_path in image_paths
        if image_path.stem == stem
        or (
            image_path.stem.startswith(stem)
            and image_path.stem[len(stem) :].isdigit()
        )
    ]


def find_bird_image(
    species_name: str,
    *,
    stable: bool = False,
) -> dict[str, str] | None:
    """Return one local bird image, optionally choosing a stable variant."""

    candidates = find_bird_image_candidates(species_name)
    if not candidates:
        return None

    if stable:
        digest = sha256(species_name.encode("utf-8")).digest()
        image_path = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
    else:
        image_path = choice(candidates)
    image_version = int(image_path.stat().st_mtime)
    return {
        "filename": image_path.name,
        "url": f"/assets/birds/{image_path.name}?v={image_version}",
    }


def find_still_bird_image(species_name: str) -> dict[str, str] | None:
    """Return the unnumbered local bird image, used as the still/profile variant."""

    stem = bird_image_stem(species_name)
    for extension in ("png", "jpg", "jpeg", "webp"):
        image_path = BIRD_ASSETS_DIR / f"{stem}.{extension}"
        if image_path.exists():
            image_version = int(image_path.stat().st_mtime)
            return {
                "filename": image_path.name,
                "url": f"/assets/birds/{image_path.name}?v={image_version}",
            }
    return None


def find_bird_image_variants(species_name: str) -> list[dict[str, str]]:
    """Return all local bird image variants in stable filename order."""

    variants: list[dict[str, str]] = []
    for image_path in find_bird_image_candidates(species_name):
        image_version = int(image_path.stat().st_mtime)
        variants.append(
            {
                "filename": image_path.name,
                "url": f"/assets/birds/{image_path.name}?v={image_version}",
            }
        )
    return variants


def find_bird_image_url(species_name: str) -> str | None:
    """Return one local bird image URL if one exists."""

    image = find_bird_image(species_name)
    if image is None:
        return None
    return image["url"]


def species_summary_payload(
    summary: object,
    *,
    stable_image: bool = False,
) -> dict[str, object]:
    """Return a species summary payload with a chosen local image variant."""

    image = find_bird_image(summary.species_name, stable=stable_image)
    still_image = find_still_bird_image(summary.species_name)
    image_variants = find_bird_image_variants(summary.species_name)
    image_filename = (
        image["filename"]
        if image is not None
        else f"{bird_image_stem(summary.species_name)}.png"
    )
    return {
        "species_name": summary.species_name,
        "display_name": format_species_name(summary.species_name),
        "image_filename": image_filename,
        "image_url": image["url"] if image is not None else None,
        "still_image_url": still_image["url"] if still_image is not None else None,
        "image_variants": image_variants,
        "count": summary.count,
        "best_confidence": summary.best_confidence,
        "latest_analyzed_at": summary.latest_analyzed_at,
    }



def get_configured_microphone() -> tuple[Microphone, int, Path, int]:
    """Return microphone, sample rate, and output path from config.toml."""

    audio_config = load_audio_config(CONFIG_PATH)
    microphones = get_microphones()

    microphone = find_preferred_microphone(
        microphones,
        audio_config.device,
        audio_config.device_name,
    )
    if microphone is None:
        raise RuntimeError("Der blev ikke fundet en brugbar input-mikrofon.")

    sample_rate = audio_config.sample_rate or microphone.default_samplerate
    output_path = build_output_path(audio_config.output_dir)
    return microphone, sample_rate, output_path, audio_config.recordings_to_keep


def format_toml_value(value: int | float | str | bool) -> str:
    """Format simple Python values for config.toml."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return str(value)


def write_config_values(
    path: Path,
    updates: dict[str, dict[str, int | float | str | bool]],
) -> None:
    """Update selected config.toml values while preserving other lines."""

    if not path.exists():
        raise RuntimeError(f"{path} blev ikke fundet.")

    lines = path.read_text(encoding="utf-8").splitlines()
    output_lines: list[str] = []
    current_section: str | None = None
    section_found: set[str] = set()
    written_keys: dict[str, set[str]] = {section: set() for section in updates}

    def append_missing_section_keys(section: str | None) -> None:
        if section is None or section not in updates:
            return

        missing_keys = set(updates[section]) - written_keys[section]
        for key in updates[section]:
            if key in missing_keys:
                output_lines.append(f"{key} = {format_toml_value(updates[section][key])}")
                written_keys[section].add(key)

    for line in lines:
        stripped = line.strip()
        is_section = stripped.startswith("[") and stripped.endswith("]")

        if is_section:
            append_missing_section_keys(current_section)
            current_section = stripped[1:-1]
            section_found.add(current_section)
            output_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if current_section in updates and key in updates[current_section]:
            prefix = line[: len(line) - len(line.lstrip())]
            value = format_toml_value(updates[current_section][key])
            output_lines.append(f"{prefix}{key} = {value}")
            written_keys[current_section].add(key)
            continue

        output_lines.append(line)

    append_missing_section_keys(current_section)

    for section, section_updates in updates.items():
        if section in section_found:
            continue
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        output_lines.append(f"[{section}]")
        for key, value in section_updates.items():
            output_lines.append(f"{key} = {format_toml_value(value)}")

    path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def write_audio_device_to_config(path: Path, microphone: Microphone) -> None:
    """Update the preferred audio device in config.toml."""

    write_config_values(
        path,
        {"audio": {"device": microphone.index, "device_name": microphone.name}},
    )


def validate_time_value(value: str, field_name: str) -> str:
    """Validate and normalize a HH:MM time value."""

    if not fullmatch(r"\d{2}:\d{2}", value):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} skal have formatet HH:MM.",
        )

    hour_text, minute_text = value.split(":")
    hour = int(hour_text)
    minute = int(minute_text)
    if hour > 23 or minute > 59:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} skal være et gyldigt klokkeslæt.",
        )

    return f"{hour:02d}:{minute:02d}"


def load_wall_config(config: dict[str, object]) -> dict[str, int | float | bool | str]:
    """Return wall display settings with safe defaults."""

    wall_config = config.get("wall", {})
    if not isinstance(wall_config, dict):
        wall_config = {}

    max_species = wall_config.get("max_species", DEFAULT_WALL_MAX_SPECIES)
    recent_minutes = wall_config.get(
        "recent_minutes",
        DEFAULT_WALL_RECENT_MINUTES,
    )
    show_names = wall_config.get("show_names", DEFAULT_WALL_SHOW_NAMES)
    show_latin_names = wall_config.get(
        "show_latin_names",
        DEFAULT_WALL_SHOW_LATIN_NAMES,
    )
    show_footer = wall_config.get("show_footer", DEFAULT_WALL_SHOW_FOOTER)
    show_shadows = wall_config.get("show_shadows", DEFAULT_WALL_SHOW_SHADOWS)
    size_mode = wall_config.get("size_mode", DEFAULT_WALL_SIZE_MODE)
    min_confidence = wall_config.get(
        "min_confidence",
        DEFAULT_WALL_MIN_CONFIDENCE,
    )
    eink_background = wall_config.get(
        "eink_background",
        DEFAULT_WALL_EINK_BACKGROUND,
    )
    if not isinstance(max_species, int) or max_species < 1:
        max_species = DEFAULT_WALL_MAX_SPECIES
    if not isinstance(recent_minutes, int) or recent_minutes < 1:
        recent_minutes = DEFAULT_WALL_RECENT_MINUTES
    if not isinstance(show_names, bool):
        show_names = DEFAULT_WALL_SHOW_NAMES
    if not isinstance(show_latin_names, bool):
        show_latin_names = DEFAULT_WALL_SHOW_LATIN_NAMES
    if not isinstance(show_footer, bool):
        show_footer = DEFAULT_WALL_SHOW_FOOTER
    if not isinstance(show_shadows, bool):
        show_shadows = DEFAULT_WALL_SHOW_SHADOWS
    if not isinstance(size_mode, str) or size_mode not in WALL_SIZE_MODES:
        size_mode = DEFAULT_WALL_SIZE_MODE
    if not isinstance(min_confidence, int | float) or not 0 <= min_confidence <= 1:
        min_confidence = DEFAULT_WALL_MIN_CONFIDENCE
    if not isinstance(eink_background, str) or fullmatch(
        r"#[0-9A-Fa-f]{6}",
        eink_background,
    ) is None:
        eink_background = DEFAULT_WALL_EINK_BACKGROUND

    return {
        "max_species": max_species,
        "recent_minutes": recent_minutes,
        "min_confidence": float(min_confidence),
        "show_names": show_names,
        "show_latin_names": show_latin_names,
        "show_footer": show_footer,
        "show_shadows": show_shadows,
        "size_mode": size_mode,
        "eink_background": eink_background.upper(),
    }


def load_site_config(config: dict[str, object]) -> dict[str, str]:
    """Return site-wide display settings with safe defaults."""

    site_config = config.get("site", {})
    if not isinstance(site_config, dict):
        site_config = {}

    title = site_config.get("title", DEFAULT_SITE_TITLE)
    if not isinstance(title, str) or not title.strip():
        title = DEFAULT_SITE_TITLE

    return {"title": title.strip()}


def load_birdnet_runtime_config(config: dict[str, object]) -> dict[str, float]:
    """Return BirdNET runtime thresholds with safe defaults."""

    birdnet_config = config.get("birdnet", {})
    if not isinstance(birdnet_config, dict):
        birdnet_config = {}

    min_confidence = birdnet_config.get("min_confidence", DEFAULT_CONFIDENCE)
    geo_min_confidence = birdnet_config.get("geo_min_confidence", 0.05)
    if not isinstance(min_confidence, int | float) or not 0 <= min_confidence <= 1:
        min_confidence = DEFAULT_CONFIDENCE
    if (
        not isinstance(geo_min_confidence, int | float)
        or not 0 <= geo_min_confidence <= 1
    ):
        geo_min_confidence = 0.05

    return {
        "min_confidence": float(min_confidence),
        "geo_min_confidence": float(geo_min_confidence),
    }


def wall_payload(
    limit: int | None = None,
    min_confidence: float | None = None,
    recent_minutes: int | None = None,
    *,
    stable_images: bool = False,
) -> dict[str, object]:
    """Return species data shaped for wall-like displays."""

    config = load_config(CONFIG_PATH)
    site_config = load_site_config(config)
    wall_config = load_wall_config(config)
    wall_limit = limit or wall_config["max_species"]
    wall_recent_minutes = recent_minutes or wall_config["recent_minutes"]
    wall_min_confidence = (
        min_confidence
        if min_confidence is not None
        else wall_config["min_confidence"]
    )
    since_analyzed_at = (
        datetime.now() - timedelta(minutes=wall_recent_minutes)
    ).isoformat(timespec="seconds")

    database_path = load_database_path(CONFIG_PATH)
    species_summary = get_species_summary(
        database_path,
        wall_limit,
        wall_min_confidence,
        since_analyzed_at=since_analyzed_at,
        exclusive_min_confidence=True,
    )
    using_recent_window = True
    if not species_summary:
        species_summary = get_species_summary(
            database_path,
            wall_limit,
            wall_min_confidence,
            exclusive_min_confidence=True,
        )
        using_recent_window = False

    return {
        "count": len(species_summary),
        "site_title": site_config["title"],
        "min_confidence": wall_min_confidence,
        "limit": wall_limit,
        "recent_minutes": wall_recent_minutes,
        "using_recent_window": using_recent_window,
        "show_names": wall_config["show_names"],
        "show_latin_names": wall_config["show_latin_names"],
        "show_footer": wall_config["show_footer"],
        "show_shadows": wall_config["show_shadows"],
        "size_mode": wall_config["size_mode"],
        "eink_background": wall_config["eink_background"],
        "updated_at": now_iso(),
        "species": [
            species_summary_payload(summary, stable_image=stable_images)
            for summary in species_summary
        ],
    }


app = FastAPI(title="Fuglestation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


def scheduler_is_running() -> bool:
    """Return whether the web server currently owns a live scheduler process."""

    return scheduler_process is not None and scheduler_process.poll() is None


def scheduler_response(message: str) -> dict[str, object]:
    """Return a small scheduler control response."""

    status = read_status(DEFAULT_STATUS_PATH)
    return {
        "running": scheduler_is_running(),
        "message": message,
        "status": {
            "state": status.state,
            "message": status.message,
            "updated_at": status.updated_at,
            "cycles_run": status.cycles_run,
            "last_cycle_started_at": status.last_cycle_started_at,
            "last_cycle_finished_at": status.last_cycle_finished_at,
            "next_cycle_at": status.next_cycle_at,
            "last_error": status.last_error,
        },
    }


@app.get("/")
def wall_root() -> FileResponse:
    """Serve the calm fullscreen wall display as the front page."""

    return FileResponse(STATIC_DIR / "wall.html")


@app.get("/settings")
@app.get("/settings/")
def index() -> FileResponse:
    """Serve the settings/dashboard UI."""

    return FileResponse(STATIC_DIR / "index.html")


@app.get("/stats")
def stats() -> FileResponse:
    """Serve the mobile-optimized statistics display."""

    return FileResponse(STATIC_DIR / "stats.html")


@app.get("/stats/species")
def species_stats() -> FileResponse:
    """Serve the mobile-optimized single-species statistics display."""

    return FileResponse(STATIC_DIR / "species_stats.html")


@app.get("/api/detections")
def api_detections(
    limit: int = Query(default=25, ge=1, le=10000),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
) -> dict[str, object]:
    """Return recent detections from SQLite."""

    config = load_config(CONFIG_PATH)
    birdnet_runtime_config = load_birdnet_runtime_config(config)
    detection_min_confidence = (
        min_confidence
        if min_confidence is not None
        else birdnet_runtime_config["min_confidence"]
    )
    database_path = load_database_path(CONFIG_PATH)
    detections = get_recent_detections(
        database_path,
        limit,
        detection_min_confidence,
    )
    overview = get_detection_overview(
        database_path,
        min_confidence=detection_min_confidence,
    )
    species_summary = get_species_summary(
        database_path,
        10,
        detection_min_confidence,
    )

    return {
        "database": str(database_path),
        "count": overview.detection_count,
        "species_count": overview.species_count,
        "min_confidence": detection_min_confidence,
        "species_summary": [
            species_summary_payload(summary) for summary in species_summary
        ],
        "detections": [
            {
                "recording_path": detection.recording_path,
                "recording_name": Path(detection.recording_path).name,
                "start_time": detection.start_time,
                "end_time": detection.end_time,
                "species_name": detection.species_name,
                "display_name": format_species_name(detection.species_name),
                "confidence": detection.confidence,
                "analyzed_at": detection.analyzed_at,
            }
            for detection in detections
        ],
    }


@app.get("/api/stats")
def api_stats(
    days: int = Query(default=30, ge=0, le=366),
    limit: int = Query(default=24, ge=1, le=80),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    hour: int | None = Query(default=None, ge=0, le=23),
) -> dict[str, object]:
    """Return detection statistics for the mobile statistics view."""

    since_analyzed_at = None
    if days > 0:
        since_analyzed_at = (datetime.now() - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
    database_path = load_database_path(CONFIG_PATH)
    config = load_config(CONFIG_PATH)
    site_config = load_site_config(config)
    birdnet_runtime_config = load_birdnet_runtime_config(config)
    stats_min_confidence = (
        min_confidence
        if min_confidence is not None
        else birdnet_runtime_config["min_confidence"]
    )
    overview = get_detection_overview(
        database_path,
        min_confidence=stats_min_confidence,
        since_analyzed_at=since_analyzed_at,
        hour=hour,
    )
    species_statistics = get_species_statistics(
        database_path,
        limit=limit,
        min_confidence=stats_min_confidence,
        since_analyzed_at=since_analyzed_at,
        hour=hour,
    )
    hourly_species_statistics = get_species_statistics(
        database_path,
        limit=80,
        min_confidence=stats_min_confidence,
        since_analyzed_at=since_analyzed_at,
    )

    all_hourly_counts = [0] * 24
    for species in hourly_species_statistics:
        for hour_index, count in species.hourly_counts.items():
            if 0 <= hour_index <= 23:
                all_hourly_counts[hour_index] += count

    return {
        "database": str(database_path),
        "site_title": site_config["title"],
        "days": days,
        "limit": limit,
        "min_confidence": stats_min_confidence,
        "hour": hour,
        "updated_at": now_iso(),
        "overview": {
            "detection_count": overview.detection_count,
            "species_count": overview.species_count,
            "recording_count": overview.recording_count,
            "first_analyzed_at": overview.first_analyzed_at,
            "latest_analyzed_at": overview.latest_analyzed_at,
        },
        "hourly_counts": all_hourly_counts,
        "species": [
            {
                **species_summary_payload(species),
                "recording_count": species.recording_count,
                "first_analyzed_at": species.first_analyzed_at,
                "hourly_counts": [
                    species.hourly_counts.get(hour, 0) for hour in range(24)
                ],
                "daily_counts": [
                    {"date": date, "count": count}
                    for date, count in species.daily_counts.items()
                ],
            }
            for species in species_statistics
        ],
    }


@app.get("/api/stats/species")
def api_species_stats(
    species_name: str = Query(min_length=1),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    hour: int | None = Query(default=None, ge=0, le=23),
    month: int | None = Query(default=None, ge=1, le=12),
) -> dict[str, object]:
    """Return all-time statistics for one species."""

    database_path = load_database_path(CONFIG_PATH)
    config = load_config(CONFIG_PATH)
    site_config = load_site_config(config)
    birdnet_runtime_config = load_birdnet_runtime_config(config)
    stats_min_confidence = (
        min_confidence
        if min_confidence is not None
        else birdnet_runtime_config["min_confidence"]
    )
    base_species = get_species_statistics_for_name(
        database_path,
        species_name=species_name,
        min_confidence=stats_min_confidence,
    )
    if base_species is None:
        raise HTTPException(status_code=404, detail="Arten blev ikke fundet.")
    species = get_species_statistics_for_name(
        database_path,
        species_name=species_name,
        min_confidence=stats_min_confidence,
        hour=hour,
        month=month,
    )
    hour_distribution_species = get_species_statistics_for_name(
        database_path,
        species_name=species_name,
        min_confidence=stats_min_confidence,
        month=month,
    )
    month_distribution_species = get_species_statistics_for_name(
        database_path,
        species_name=species_name,
        min_confidence=stats_min_confidence,
        hour=hour,
    )
    if species is None:
        species = base_species
        species_count = 0
        species_recording_count = 0
        species_best_confidence = 0.0
        species_latest_confidence = 0.0
        species_first_analyzed_at = None
        species_latest_analyzed_at = None
        species_latest_recording_path = Path("")
        species_match_history = []
    else:
        species_count = species.count
        species_recording_count = species.recording_count
        species_best_confidence = species.best_confidence
        species_latest_confidence = species.latest_confidence
        species_first_analyzed_at = species.first_analyzed_at
        species_latest_analyzed_at = species.latest_analyzed_at
        species_latest_recording_path = Path(species.latest_recording_path)
        species_match_history = species.match_history

    recordings_dir = load_recordings_dir(CONFIG_PATH).resolve()
    latest_recording_path = species_latest_recording_path

    def recording_url(recording_path: Path) -> str | None:
        if not recording_path.name:
            return None
        resolved_recording_path = recording_path.resolve()
        if (
            resolved_recording_path.parent == recordings_dir
            and resolved_recording_path.exists()
        ):
            return f"/api/audio/recordings/{resolved_recording_path.name}"
        return None

    latest_clip = load_species_clip_index(DEFAULT_SPECIES_CLIPS_DIR).get(
        species.species_name,
    )
    latest_audio_url = None
    if latest_clip and latest_clip.get("filename"):
        latest_audio_url = f"/api/audio/species-clips/{latest_clip['filename']}"
    if latest_audio_url is None:
        latest_audio_url = recording_url(latest_recording_path)
    if species_count == 0:
        latest_audio_url = None

    return {
        "database": str(database_path),
        "site_title": site_config["title"],
        "min_confidence": stats_min_confidence,
        "hour": hour,
        "month": month,
        "updated_at": now_iso(),
        "species": {
            **{
                **species_summary_payload(base_species),
                "count": species_count,
                "best_confidence": species_best_confidence,
                "latest_analyzed_at": species_latest_analyzed_at,
            },
            "recording_count": species_recording_count,
            "latest_confidence": species_latest_confidence,
            "first_analyzed_at": species_first_analyzed_at,
            "latest_analyzed_at": species_latest_analyzed_at,
            "latest_recording_name": latest_recording_path.name,
            "latest_recording_url": latest_audio_url,
            "match_history": [
                {
                    "analyzed_at": match.analyzed_at,
                    "confidence": match.confidence,
                    "recording_name": Path(match.recording_path).name,
                    "recording_url": recording_url(Path(match.recording_path)),
                }
                for match in species_match_history
            ],
            "hourly_counts": [
                (hour_distribution_species or base_species).hourly_counts.get(hour, 0)
                for hour in range(24)
            ],
            "monthly_counts": [
                (month_distribution_species or base_species).monthly_counts.get(month, 0)
                for month in range(1, 13)
            ],
        },
    }


@app.get("/api/wall")
def api_wall(
    limit: int | None = Query(default=None, ge=1, le=60),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    recent_minutes: int | None = Query(default=None, ge=1, le=10080),
) -> dict[str, object]:
    """Return species data shaped for the wall display."""

    return wall_payload(limit, min_confidence, recent_minutes)


@lru_cache(maxsize=32)
def render_cached_eink_image(
    payload_json: str,
    output_format: str,
    landscape: bool,
    width: int | None,
    height: int | None,
    use_spectra_palette: bool,
) -> bytes:
    """Render an eInk image once for each distinct visible wall state."""

    payload = json.loads(payload_json)
    return render_eink_wall_image(
        payload,
        BIRD_ASSETS_DIR,
        width,
        height,
        landscape=landscape,
        output_format=output_format,
        use_spectra_palette=use_spectra_palette,
    )


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Return whether an If-None-Match header accepts the current entity tag."""

    if not if_none_match:
        return False
    candidates = {value.strip() for value in if_none_match.split(",")}
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates


def eink_response(
    output_format: str,
    landscape: bool,
    width: int | None,
    height: int | None,
    limit: int | None,
    min_confidence: float | None,
    recent_minutes: int | None,
    palette: str,
    if_none_match: str | None = None,
) -> Response:
    """Return the current wall view as an eInk-friendly image."""

    if palette not in {"auto", "rgb", "spectra"}:
        raise HTTPException(
            status_code=400,
            detail="palette skal vaere auto, rgb eller spectra.",
        )
    use_spectra_palette = palette == "spectra" or (
        palette == "auto" and output_format == "PNG"
    )
    payload = wall_payload(
        limit,
        min_confidence,
        recent_minutes,
        stable_images=True,
    )
    # The request timestamp is useful to the browser API, but it is not visible in
    # the eInk image and must not invalidate an otherwise unchanged render.
    payload.pop("updated_at", None)
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    image = render_cached_eink_image(
        payload_json,
        output_format,
        landscape,
        width,
        height,
        use_spectra_palette,
    )
    etag = f'"{sha256(image).hexdigest()}"'
    media_type = "image/jpeg" if output_format == "JPEG" else "image/png"
    extension = "jpg" if output_format == "JPEG" else "png"
    headers = {
        "Cache-Control": "no-cache, max-age=0",
        "Content-Disposition": f'inline; filename="fuglestation-eink.{extension}"',
        "ETag": etag,
    }
    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return Response(content=image, media_type=media_type, headers=headers)


@app.get("/eink.png")
@app.get("/api/eink.png")
def eink_png(
    landscape: bool = False,
    width: int | None = Query(default=None, ge=320, le=2400),
    height: int | None = Query(default=None, ge=240, le=2400),
    limit: int | None = Query(default=None, ge=1, le=60),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    recent_minutes: int | None = Query(default=None, ge=1, le=10080),
    palette: str = Query(default="auto"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """Return the current wall view as a PNG for an eInk controller."""

    return eink_response(
        "PNG",
        landscape,
        width,
        height,
        limit,
        min_confidence,
        recent_minutes,
        palette,
        if_none_match,
    )


@app.get("/eink.jpg")
@app.get("/api/eink.jpg")
def eink_jpg(
    landscape: bool = False,
    width: int | None = Query(default=None, ge=320, le=2400),
    height: int | None = Query(default=None, ge=240, le=2400),
    limit: int | None = Query(default=None, ge=1, le=60),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    recent_minutes: int | None = Query(default=None, ge=1, le=10080),
    palette: str = Query(default="auto"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    """Return the current wall view as a JPEG for an eInk controller."""

    return eink_response(
        "JPEG",
        landscape,
        width,
        height,
        limit,
        min_confidence,
        recent_minutes,
        palette,
        if_none_match,
    )


@app.get("/api/status")
def api_status() -> dict[str, object]:
    """Return scheduler/runtime status for the station UI."""

    status = read_status(DEFAULT_STATUS_PATH)
    return {
        "scheduler_process_running": scheduler_is_running(),
        "state": status.state,
        "message": status.message,
        "updated_at": status.updated_at,
        "cycles_run": status.cycles_run,
        "last_cycle_started_at": status.last_cycle_started_at,
        "last_cycle_finished_at": status.last_cycle_finished_at,
        "next_cycle_at": status.next_cycle_at,
        "last_error": status.last_error,
    }


@app.get("/api/config")
def api_config() -> dict[str, object]:
    """Return the current config.toml values for display."""

    config = load_config(CONFIG_PATH)
    audio_config = config.get("audio", {})
    birdnet_config = config.get("birdnet", {})
    database_config = config.get("database", {})
    schedule_config = config.get("schedule", {})
    site_config = load_site_config(config)
    wall_config = load_wall_config(config)

    if not isinstance(audio_config, dict):
        audio_config = {}
    if not isinstance(birdnet_config, dict):
        birdnet_config = {}
    if not isinstance(database_config, dict):
        database_config = {}
    if not isinstance(schedule_config, dict):
        schedule_config = {}
    birdnet_runtime_config = load_birdnet_runtime_config(config)

    return {
        "config_path": str(CONFIG_PATH),
        "site": site_config,
        "audio": {
            "device": audio_config.get("device", 0),
            "device_name": audio_config.get("device_name", ""),
            "duration_seconds": audio_config.get("duration_seconds", 10),
            "recordings_to_keep": audio_config.get("recordings_to_keep", 3),
            "sample_rate": audio_config.get("sample_rate", 44100),
            "output_dir": audio_config.get("output_dir", "recordings"),
        },
        "birdnet": {
            "use_geo": birdnet_config.get("use_geo", True),
            "latitude": birdnet_config.get("latitude", 56.0),
            "longitude": birdnet_config.get("longitude", 10.0),
            "week": birdnet_config.get("week", 0),
            "min_confidence": birdnet_runtime_config["min_confidence"],
            "geo_min_confidence": birdnet_runtime_config["geo_min_confidence"],
        },
        "database": {
            "path": database_config.get("path", str(DEFAULT_DATABASE_PATH)),
        },
        "schedule": {
            "first_phase_seconds": schedule_config.get("first_phase_seconds", 120),
            "first_phase_interval_seconds": schedule_config.get(
                "first_phase_interval_seconds",
                30,
            ),
            "second_phase_seconds": schedule_config.get("second_phase_seconds", 600),
            "second_phase_interval_seconds": schedule_config.get(
                "second_phase_interval_seconds",
                60,
            ),
            "steady_interval_seconds": schedule_config.get(
                "steady_interval_seconds",
                900,
            ),
            "quiet_start": schedule_config.get("quiet_start", "22:00"),
            "quiet_end": schedule_config.get("quiet_end", "05:00"),
        },
        "wall": wall_config,
    }


@app.get("/api/audio/devices")
def api_audio_devices() -> dict[str, object]:
    """Return available microphone input devices."""

    config = api_config()
    audio_config = config["audio"]
    configured_device = audio_config["device"]
    configured_device_name = audio_config.get("device_name")
    microphones = get_microphones()
    selected_microphone = find_preferred_microphone(
        microphones,
        configured_device,
        configured_device_name if isinstance(configured_device_name, str) else None,
    )
    selected_index = selected_microphone.index if selected_microphone else None
    device_payload = [
        {
            "index": microphone.index,
            "name": microphone.name,
            "host_api": microphone.host_api,
            "max_input_channels": microphone.max_input_channels,
            "default_samplerate": microphone.default_samplerate,
            "configured": microphone.index == selected_index,
        }
        for microphone in microphones
    ]

    if (
        not device_payload
        and isinstance(configured_device, int)
        and isinstance(configured_device_name, str)
        and configured_device_name
    ):
        device_payload.append(
            {
                "index": configured_device,
                "name": configured_device_name,
                "host_api": "Gemt valg",
                "max_input_channels": 1,
                "default_samplerate": audio_config["sample_rate"],
                "configured": True,
            }
        )

    return {
        "configured_device": configured_device,
        "configured_device_name": configured_device_name,
        "selected_device": selected_index,
        "count": len(microphones),
        "devices": device_payload,
    }


@app.get("/api/audio/recordings")
def api_audio_recordings(
    limit: int = Query(default=3, ge=1, le=500),
) -> dict[str, object]:
    """Return the newest locally retained WAV recordings."""

    recordings_dir = load_recordings_dir(CONFIG_PATH)
    if not recordings_dir.exists():
        return {
            "recordings_dir": str(recordings_dir),
            "count": 0,
            "recordings": [],
        }

    recordings = sorted(
        recordings_dir.glob("*.wav"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]

    return {
        "recordings_dir": str(recordings_dir),
        "count": len(recordings),
        "recordings": [
            {
                "filename": recording.name,
                "url": f"/api/audio/recordings/{recording.name}",
                "modified_at": datetime.fromtimestamp(
                    recording.stat().st_mtime,
                ).isoformat(timespec="seconds"),
                "size_bytes": recording.stat().st_size,
            }
            for recording in recordings
        ],
    }


@app.get("/api/audio/recordings/{filename}")
def api_audio_recording_file(filename: str) -> FileResponse:
    """Serve one retained WAV recording from the configured recordings folder."""

    if Path(filename).name != filename or not filename.lower().endswith(".wav"):
        raise HTTPException(status_code=404, detail="Optagelsen blev ikke fundet.")

    recordings_dir = load_recordings_dir(CONFIG_PATH).resolve()
    recording_path = (recordings_dir / filename).resolve()
    if recording_path.parent != recordings_dir or not recording_path.exists():
        raise HTTPException(status_code=404, detail="Optagelsen blev ikke fundet.")

    return FileResponse(recording_path, media_type="audio/wav")


@app.get("/api/audio/species-clips")
def api_audio_species_clips() -> dict[str, object]:
    """Return the latest saved audio clip for each heard species."""

    clips_dir = DEFAULT_SPECIES_CLIPS_DIR
    index = load_species_clip_index(clips_dir)
    clips = sorted(
        index.values(),
        key=lambda clip: str(clip.get("updated_at", "")),
        reverse=True,
    )

    return {
        "clips_dir": str(clips_dir),
        "count": len(clips),
        "clips": [
            {
                **clip,
                "display_name": format_species_name(str(clip["species_name"])),
                "url": f"/api/audio/species-clips/{clip['filename']}",
            }
            for clip in clips
            if "species_name" in clip and "filename" in clip
        ],
    }


@app.get("/api/audio/species-clips/{filename}")
def api_audio_species_clip_file(filename: str) -> FileResponse:
    """Serve one latest-species WAV clip."""

    if Path(filename).name != filename or not filename.lower().endswith(".wav"):
        raise HTTPException(status_code=404, detail="Artsklippet blev ikke fundet.")

    clips_dir = DEFAULT_SPECIES_CLIPS_DIR.resolve()
    clip_path = (clips_dir / filename).resolve()
    if clip_path.parent != clips_dir or not clip_path.exists():
        raise HTTPException(status_code=404, detail="Artsklippet blev ikke fundet.")

    return FileResponse(clip_path, media_type="audio/wav")


@app.delete("/api/audio/species-clips/{filename}")
def api_delete_audio_species_clip(filename: str) -> dict[str, object]:
    """Delete a species clip and the one detection it represents."""

    if Path(filename).name != filename or not filename.lower().endswith(".wav"):
        raise HTTPException(status_code=404, detail="Artsklippet blev ikke fundet.")

    clips_dir = DEFAULT_SPECIES_CLIPS_DIR.resolve()
    index = load_species_clip_index(clips_dir)
    matches = [
        (species_name, clip)
        for species_name, clip in index.items()
        if clip.get("filename") == filename
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="Artsklippet blev ikke fundet.")

    species_name, clip = matches[0]
    clip_path = (clips_dir / filename).resolve()
    if clip_path.parent != clips_dir:
        raise HTTPException(status_code=404, detail="Artsklippet blev ikke fundet.")

    staged_path = clips_dir / f".{filename}.deleting"
    if staged_path.exists():
        raise HTTPException(
            status_code=409,
            detail="En tidligere sletning af artsklippet er ikke afsluttet.",
        )

    original_index = dict(index)
    file_staged = False
    try:
        if clip_path.exists():
            clip_path.replace(staged_path)
            file_staged = True
        del index[species_name]
        save_species_clip_index(clips_dir, index)
        detection_id = delete_species_clip_detection(
            database_path=load_database_path(CONFIG_PATH),
            species_name=species_name,
            source_recording=str(clip["source_recording"]),
            confidence=float(clip["confidence"]),
            clip_start_time=float(clip["start_time"]),
            clip_end_time=float(clip["end_time"]),
            detection_start_time=(
                float(clip["detection_start_time"])
                if "detection_start_time" in clip
                else None
            ),
            detection_end_time=(
                float(clip["detection_end_time"])
                if "detection_end_time" in clip
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        save_species_clip_index(clips_dir, original_index)
        if file_staged and staged_path.exists() and not clip_path.exists():
            staged_path.replace(clip_path)
        raise HTTPException(status_code=400, detail=str(error)) from error

    if file_staged:
        staged_path.unlink()

    return {
        "message": (
            "Artsklip og detektion slettet."
            if detection_id is not None
            else "Artsklip slettet; detektionen var allerede fjernet."
        ),
        "filename": filename,
        "species_name": species_name,
        "detection_id": detection_id,
    }


@app.post("/api/audio/test-recording")
def api_audio_test_recording() -> dict[str, object]:
    """Record a short WAV file with the configured microphone."""

    try:
        microphone, sample_rate, output_path, recordings_to_keep = (
            get_configured_microphone()
        )
        record_audio(
            microphone=microphone,
            duration_seconds=TEST_RECORDING_SECONDS,
            sample_rate=sample_rate,
            output_path=output_path,
            recordings_to_keep=recordings_to_keep,
        )
    except (RuntimeError, SystemExit) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "message": "Testoptagelse gemt.",
        "recording_path": str(output_path),
        "recording_name": output_path.name,
        "duration_seconds": TEST_RECORDING_SECONDS,
        "sample_rate": sample_rate,
        "microphone": {
            "index": microphone.index,
            "name": microphone.name,
            "host_api": microphone.host_api,
        },
    }


@app.post("/api/config/audio-device")
def api_config_audio_device(update: AudioDeviceUpdate) -> dict[str, object]:
    """Store the selected microphone device in config.toml."""

    microphones = get_microphones()
    microphone_by_index = {microphone.index: microphone for microphone in microphones}
    microphone = microphone_by_index.get(update.device)

    if microphone is None:
        raise HTTPException(
            status_code=400,
            detail=f"Mikrofonnummer {update.device} blev ikke fundet.",
        )

    try:
        write_audio_device_to_config(CONFIG_PATH, microphone)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "message": "Mikrofonvalg gemt.",
        "device": update.device,
        "microphone": {
            "index": microphone.index,
            "name": microphone.name,
            "host_api": microphone.host_api,
        },
    }


@app.post("/api/config/runtime-settings")
def api_config_runtime_settings(update: RuntimeSettingsUpdate) -> dict[str, object]:
    """Store core runtime settings in config.toml."""

    site_title = update.site_title.strip()
    if len(site_title) < 1 or len(site_title) > 80:
        raise HTTPException(
            status_code=400,
            detail="Titel skal være mellem 1 og 80 tegn.",
        )
    if update.duration_seconds < 1 or update.duration_seconds > 300:
        raise HTTPException(
            status_code=400,
            detail="Optagelængde skal være mellem 1 og 300 sekunder.",
        )
    if update.recordings_to_keep < 1 or update.recordings_to_keep > 500:
        raise HTTPException(
            status_code=400,
            detail="Antal gemte optagelser skal være mellem 1 og 500.",
        )
    if update.birdnet_min_confidence < 0 or update.birdnet_min_confidence > 1:
        raise HTTPException(
            status_code=400,
            detail="Registrerings-confidence skal være mellem 0 og 1.",
        )
    if update.wall_min_confidence < 0 or update.wall_min_confidence > 1:
        raise HTTPException(
            status_code=400,
            detail="Væg-confidence skal være mellem 0 og 1.",
        )
    if update.wall_max_species < 1 or update.wall_max_species > 60:
        raise HTTPException(
            status_code=400,
            detail="Maks fugle på væggen skal være mellem 1 og 60.",
        )
    if update.wall_recent_minutes < 1 or update.wall_recent_minutes > 10080:
        raise HTTPException(
            status_code=400,
            detail="Væggens tidsvindue skal være mellem 1 minut og 7 dage.",
        )
    if update.wall_size_mode not in WALL_SIZE_MODES:
        raise HTTPException(
            status_code=400,
            detail="Vægstørrelse skal være equal, common eller rare.",
        )
    if fullmatch(r"#[0-9A-Fa-f]{6}", update.wall_eink_background) is None:
        raise HTTPException(
            status_code=400,
            detail="E-ink-baggrund skal være en farve som #FBF2D6.",
        )

    birdnet_min_confidence = round(update.birdnet_min_confidence, 3)
    wall_min_confidence = round(update.wall_min_confidence, 3)
    quiet_start = validate_time_value(update.quiet_start, "quiet_start")
    quiet_end = validate_time_value(update.quiet_end, "quiet_end")
    wall_eink_background = update.wall_eink_background.upper()

    try:
        write_config_values(
            CONFIG_PATH,
            {
                "site": {"title": site_title},
                "audio": {
                    "duration_seconds": update.duration_seconds,
                    "recordings_to_keep": update.recordings_to_keep,
                },
                "birdnet": {"min_confidence": birdnet_min_confidence},
                "schedule": {
                    "quiet_start": quiet_start,
                    "quiet_end": quiet_end,
                },
                "wall": {
                    "max_species": update.wall_max_species,
                    "recent_minutes": update.wall_recent_minutes,
                    "min_confidence": wall_min_confidence,
                    "show_names": update.wall_show_names,
                    "show_latin_names": update.wall_show_latin_names,
                    "show_footer": update.wall_show_footer,
                    "show_shadows": update.wall_show_shadows,
                    "size_mode": update.wall_size_mode,
                    "eink_background": wall_eink_background,
                },
            },
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "message": "Indstillinger gemt.",
        "site_title": site_title,
        "duration_seconds": update.duration_seconds,
        "recordings_to_keep": update.recordings_to_keep,
        "birdnet_min_confidence": birdnet_min_confidence,
        "wall_min_confidence": wall_min_confidence,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "wall_max_species": update.wall_max_species,
        "wall_recent_minutes": update.wall_recent_minutes,
        "wall_show_names": update.wall_show_names,
        "wall_show_latin_names": update.wall_show_latin_names,
        "wall_show_footer": update.wall_show_footer,
        "wall_show_shadows": update.wall_show_shadows,
        "wall_size_mode": update.wall_size_mode,
        "wall_eink_background": wall_eink_background,
    }


@app.post("/api/config/reset-defaults")
def api_config_reset_defaults() -> dict[str, object]:
    """Reset config.toml to the project's default configuration."""

    if not DEFAULT_CONFIG_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Default-konfiguration mangler: {DEFAULT_CONFIG_PATH}",
        )

    copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)
    return {
        "message": "Indstillinger nulstillet til default.",
        "config_path": str(CONFIG_PATH),
        "default_config_path": str(DEFAULT_CONFIG_PATH),
    }


@app.post("/api/scheduler/start")
def api_scheduler_start() -> dict[str, object]:
    """Start the scheduler as a local background process."""

    global scheduler_process

    if scheduler_is_running():
        return scheduler_response("Scheduler kører allerede.")

    SCHEDULER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = SCHEDULER_LOG_PATH.open("a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    write_status(
        DEFAULT_STATUS_PATH,
        StationStatus(
            state="starting",
            message="Scheduler startes fra websiden.",
            updated_at=now_iso(),
        ),
    )
    scheduler_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "fuglestation.run_scheduler",
        ],
        cwd=Path.cwd(),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    return scheduler_response("Scheduler startet.")


@app.post("/api/scheduler/stop")
def api_scheduler_stop() -> dict[str, object]:
    """Stop the scheduler process started by this web server."""

    global scheduler_process

    if not scheduler_is_running():
        write_status(
            DEFAULT_STATUS_PATH,
            StationStatus(
                state="stopped",
                message="Scheduler er stoppet.",
                updated_at=now_iso(),
            ),
        )
        scheduler_process = None
        return scheduler_response("Scheduler kørte ikke.")

    scheduler_process.terminate()
    try:
        scheduler_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        scheduler_process.kill()
        scheduler_process.wait(timeout=10)

    scheduler_process = None
    write_status(
        DEFAULT_STATUS_PATH,
        StationStatus(
            state="stopped",
            message="Scheduler stoppet fra websiden.",
            updated_at=now_iso(),
        ),
    )
    return scheduler_response("Scheduler stoppet.")
