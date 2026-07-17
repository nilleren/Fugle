from __future__ import annotations

import subprocess
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from random import choice
from re import fullmatch
from re import sub
from shutil import copyfile

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fuglestation.database import (
    DEFAULT_DATABASE_PATH,
    get_detection_overview,
    get_recent_detections,
    get_species_summary,
    get_species_statistics,
    get_species_statistics_for_name,
)
from fuglestation.record_audio import build_output_path
from fuglestation.record_audio import Microphone
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
    geo_min_confidence: float
    quiet_start: str
    quiet_end: str
    wall_max_species: int
    wall_recent_minutes: int
    wall_show_names: bool
    wall_show_latin_names: bool
    wall_show_footer: bool
    wall_show_shadows: bool
    wall_size_mode: str


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


def find_bird_image(species_name: str) -> dict[str, str] | None:
    """Return one local bird image, choosing randomly between variants."""

    candidates = find_bird_image_candidates(species_name)
    if not candidates:
        return None

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


def species_summary_payload(summary: object) -> dict[str, object]:
    """Return a species summary payload with a chosen local image variant."""

    image = find_bird_image(summary.species_name)
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



def get_configured_microphone() -> tuple[Microphone, int, Path]:
    """Return microphone, sample rate, and output path from config.toml."""

    audio_config = load_audio_config(CONFIG_PATH)
    microphones = get_microphones()
    microphone_by_index = {microphone.index: microphone for microphone in microphones}

    if audio_config.device is None:
        raise RuntimeError("audio.device mangler i config.toml.")

    microphone = microphone_by_index.get(audio_config.device)
    if microphone is None:
        raise RuntimeError(
            f"Mikrofonnummer {audio_config.device} blev ikke fundet."
        )

    sample_rate = audio_config.sample_rate or microphone.default_samplerate
    output_path = build_output_path(audio_config.output_dir)
    return microphone, sample_rate, output_path


def format_toml_value(value: int | float | str | bool) -> str:
    """Format simple Python values for config.toml."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
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


def write_audio_device_to_config(path: Path, device: int) -> None:
    """Update only audio.device in config.toml."""

    write_config_values(path, {"audio": {"device": device}})


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


def load_wall_config(config: dict[str, object]) -> dict[str, int | bool]:
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

    return {
        "max_species": max_species,
        "recent_minutes": recent_minutes,
        "show_names": show_names,
        "show_latin_names": show_latin_names,
        "show_footer": show_footer,
        "show_shadows": show_shadows,
        "size_mode": size_mode,
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
    limit: int = Query(default=25, ge=1, le=200),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
) -> dict[str, object]:
    """Return recent detections from SQLite."""

    database_path = load_database_path(CONFIG_PATH)
    detections = get_recent_detections(database_path, limit, min_confidence)
    species_summary = get_species_summary(database_path, 10, min_confidence)

    return {
        "database": str(database_path),
        "count": len(detections),
        "min_confidence": min_confidence,
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
    min_confidence: float = Query(default=0.05, ge=0.0, le=1.0),
) -> dict[str, object]:
    """Return detection statistics for the mobile statistics view."""

    since_analyzed_at = None
    if days > 0:
        since_analyzed_at = (datetime.now() - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
    database_path = load_database_path(CONFIG_PATH)
    site_config = load_site_config(load_config(CONFIG_PATH))
    overview = get_detection_overview(
        database_path,
        min_confidence=min_confidence,
        since_analyzed_at=since_analyzed_at,
    )
    species_statistics = get_species_statistics(
        database_path,
        limit=limit,
        min_confidence=min_confidence,
        since_analyzed_at=since_analyzed_at,
    )

    all_hourly_counts = [0] * 24
    for species in species_statistics:
        for hour, count in species.hourly_counts.items():
            if 0 <= hour <= 23:
                all_hourly_counts[hour] += count

    return {
        "database": str(database_path),
        "site_title": site_config["title"],
        "days": days,
        "limit": limit,
        "min_confidence": min_confidence,
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
    min_confidence: float = Query(default=0.05, ge=0.0, le=1.0),
) -> dict[str, object]:
    """Return all-time statistics for one species."""

    database_path = load_database_path(CONFIG_PATH)
    site_config = load_site_config(load_config(CONFIG_PATH))
    species = get_species_statistics_for_name(
        database_path,
        species_name=species_name,
        min_confidence=min_confidence,
    )
    if species is None:
        raise HTTPException(status_code=404, detail="Arten blev ikke fundet.")

    return {
        "database": str(database_path),
        "site_title": site_config["title"],
        "min_confidence": min_confidence,
        "updated_at": now_iso(),
        "species": {
            **species_summary_payload(species),
            "recording_count": species.recording_count,
            "first_analyzed_at": species.first_analyzed_at,
            "hourly_counts": [
                species.hourly_counts.get(hour, 0) for hour in range(24)
            ],
            "monthly_counts": [
                species.monthly_counts.get(month, 0) for month in range(1, 13)
            ],
        },
    }


@app.get("/api/wall")
def api_wall(
    limit: int | None = Query(default=None, ge=1, le=60),
    min_confidence: float = Query(default=0.05, ge=0.0, le=1.0),
    recent_minutes: int | None = Query(default=None, ge=1, le=10080),
) -> dict[str, object]:
    """Return species data shaped for the wall display."""

    config = load_config(CONFIG_PATH)
    site_config = load_site_config(config)
    wall_config = load_wall_config(config)
    wall_limit = limit or wall_config["max_species"]
    wall_recent_minutes = recent_minutes or wall_config["recent_minutes"]
    since_analyzed_at = (
        datetime.now() - timedelta(minutes=wall_recent_minutes)
    ).isoformat(timespec="seconds")

    database_path = load_database_path(CONFIG_PATH)
    species_summary = get_species_summary(
        database_path,
        wall_limit,
        min_confidence,
        since_analyzed_at=since_analyzed_at,
    )

    return {
        "count": len(species_summary),
        "site_title": site_config["title"],
        "min_confidence": min_confidence,
        "limit": wall_limit,
        "recent_minutes": wall_recent_minutes,
        "show_names": wall_config["show_names"],
        "show_latin_names": wall_config["show_latin_names"],
        "show_footer": wall_config["show_footer"],
        "show_shadows": wall_config["show_shadows"],
        "size_mode": wall_config["size_mode"],
        "updated_at": now_iso(),
        "species": [species_summary_payload(summary) for summary in species_summary],
    }


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

    return {
        "config_path": str(CONFIG_PATH),
        "site": site_config,
        "audio": {
            "device": audio_config.get("device", 0),
            "duration_seconds": audio_config.get("duration_seconds", 10),
            "sample_rate": audio_config.get("sample_rate", 44100),
            "output_dir": audio_config.get("output_dir", "recordings"),
        },
        "birdnet": {
            "use_geo": birdnet_config.get("use_geo", True),
            "latitude": birdnet_config.get("latitude", 56.0),
            "longitude": birdnet_config.get("longitude", 10.0),
            "week": birdnet_config.get("week", 0),
            "geo_min_confidence": birdnet_config.get("geo_min_confidence", 0.05),
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
    microphones = get_microphones()

    return {
        "configured_device": configured_device,
        "count": len(microphones),
        "devices": [
            {
                "index": microphone.index,
                "name": microphone.name,
                "host_api": microphone.host_api,
                "max_input_channels": microphone.max_input_channels,
                "default_samplerate": microphone.default_samplerate,
                "configured": microphone.index == configured_device,
            }
            for microphone in microphones
        ],
    }


@app.post("/api/audio/test-recording")
def api_audio_test_recording() -> dict[str, object]:
    """Record a short WAV file with the configured microphone."""

    try:
        microphone, sample_rate, output_path = get_configured_microphone()
        record_audio(
            microphone=microphone,
            duration_seconds=TEST_RECORDING_SECONDS,
            sample_rate=sample_rate,
            output_path=output_path,
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
        write_audio_device_to_config(CONFIG_PATH, update.device)
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
    if update.geo_min_confidence < 0 or update.geo_min_confidence > 1:
        raise HTTPException(
            status_code=400,
            detail="BirdNET confidence skal være mellem 0 og 1.",
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

    geo_min_confidence = round(update.geo_min_confidence, 3)
    quiet_start = validate_time_value(update.quiet_start, "quiet_start")
    quiet_end = validate_time_value(update.quiet_end, "quiet_end")

    try:
        write_config_values(
            CONFIG_PATH,
            {
                "site": {"title": site_title},
                "audio": {"duration_seconds": update.duration_seconds},
                "birdnet": {"geo_min_confidence": geo_min_confidence},
                "schedule": {
                    "quiet_start": quiet_start,
                    "quiet_end": quiet_end,
                },
                "wall": {
                    "max_species": update.wall_max_species,
                    "recent_minutes": update.wall_recent_minutes,
                    "show_names": update.wall_show_names,
                    "show_latin_names": update.wall_show_latin_names,
                    "show_footer": update.wall_show_footer,
                    "show_shadows": update.wall_show_shadows,
                    "size_mode": update.wall_size_mode,
                },
            },
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "message": "Indstillinger gemt.",
        "site_title": site_title,
        "duration_seconds": update.duration_seconds,
        "geo_min_confidence": geo_min_confidence,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "wall_max_species": update.wall_max_species,
        "wall_recent_minutes": update.wall_recent_minutes,
        "wall_show_names": update.wall_show_names,
        "wall_show_latin_names": update.wall_show_latin_names,
        "wall_show_footer": update.wall_show_footer,
        "wall_show_shadows": update.wall_show_shadows,
        "wall_size_mode": update.wall_size_mode,
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
