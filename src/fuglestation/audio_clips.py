from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from re import sub
from typing import Iterable


DEFAULT_SPECIES_CLIPS_DIR = Path("data/species_clips")
CLIP_PADDING_SECONDS = 0.5


@dataclass(frozen=True)
class SpeciesClip:
    """Metadata for the newest saved audio clip for one species."""

    species_name: str
    filename: str
    source_recording: str
    start_time: float
    end_time: float
    confidence: float
    updated_at: str


def species_clip_stem(species_name: str) -> str:
    """Return a stable filename stem for a species."""

    cleaned = sub(r"[^A-Za-z0-9]+", "_", species_name).strip("_")
    return cleaned or "unknown_species"


def metadata_path(clips_dir: Path) -> Path:
    """Return the JSON index path for species clips."""

    return clips_dir / "index.json"


def load_species_clip_index(clips_dir: Path) -> dict[str, dict[str, object]]:
    """Read the species clip index if it exists."""

    index_path = metadata_path(clips_dir)
    if not index_path.exists():
        return {}

    try:
        with index_path.open("r", encoding="utf-8") as index_file:
            data = json.load(index_file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        str(species_name): clip
        for species_name, clip in data.items()
        if isinstance(clip, dict)
    }


def save_species_clip_index(
    clips_dir: Path,
    index: dict[str, dict[str, object]],
) -> None:
    """Write the species clip index."""

    clips_dir.mkdir(parents=True, exist_ok=True)
    with metadata_path(clips_dir).open("w", encoding="utf-8") as index_file:
        json.dump(index, index_file, ensure_ascii=False, indent=2, sort_keys=True)
        index_file.write("\n")


def newest_detection_per_species(detections: Iterable[object]) -> dict[str, object]:
    """Choose the latest detection in the current recording for each species."""

    selected: dict[str, object] = {}
    for detection in detections:
        species_name = str(detection.species_name)
        current = selected.get(species_name)
        if current is None or float(detection.end_time) >= float(current.end_time):
            selected[species_name] = detection
    return selected


def write_wav_clip(
    source_path: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    padding_seconds: float = CLIP_PADDING_SECONDS,
) -> tuple[float, float]:
    """Copy a time range from a WAV file into a new WAV file."""

    with wave.open(str(source_path), "rb") as source:
        frame_rate = source.getframerate()
        frame_count = source.getnframes()
        duration_seconds = frame_count / frame_rate
        clip_start = max(0.0, start_time - padding_seconds)
        clip_end = min(duration_seconds, end_time + padding_seconds)
        start_frame = int(clip_start * frame_rate)
        end_frame = max(start_frame + 1, int(clip_end * frame_rate))
        frame_total = end_frame - start_frame

        source.setpos(start_frame)
        frames = source.readframes(frame_total)
        params = source.getparams()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as output:
        output.setparams(params)
        output.writeframes(frames)

    return clip_start, clip_end


def save_latest_species_clips(
    audio_path: Path,
    detections: Iterable[object],
    clips_dir: Path = DEFAULT_SPECIES_CLIPS_DIR,
) -> list[SpeciesClip]:
    """Save or replace the newest clip for each species found in a recording."""

    if not audio_path.exists():
        return []

    selected = newest_detection_per_species(detections)
    if not selected:
        return []

    index = load_species_clip_index(clips_dir)
    updated_at = datetime.now().isoformat(timespec="seconds")
    saved_clips: list[SpeciesClip] = []

    for species_name, detection in selected.items():
        filename = f"{species_clip_stem(species_name)}.wav"
        output_path = clips_dir / filename
        clip_start, clip_end = write_wav_clip(
            source_path=audio_path,
            output_path=output_path,
            start_time=float(detection.start_time),
            end_time=float(detection.end_time),
        )
        clip = SpeciesClip(
            species_name=species_name,
            filename=filename,
            source_recording=audio_path.name,
            start_time=clip_start,
            end_time=clip_end,
            confidence=float(detection.confidence),
            updated_at=updated_at,
        )
        index[species_name] = {
            "species_name": clip.species_name,
            "filename": clip.filename,
            "source_recording": clip.source_recording,
            "start_time": clip.start_time,
            "end_time": clip.end_time,
            "confidence": clip.confidence,
            "updated_at": clip.updated_at,
        }
        saved_clips.append(clip)

    save_species_clip_index(clips_dir, index)
    return saved_clips
