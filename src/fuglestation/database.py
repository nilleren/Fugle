from __future__ import annotations

import sqlite3
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_DATABASE_PATH = Path("data/fuglestation.db")


@dataclass(frozen=True)
class StoredDetection:
    """A detection row read from SQLite."""

    recording_path: str
    start_time: float
    end_time: float
    species_name: str
    confidence: float
    analyzed_at: str


@dataclass(frozen=True)
class SpeciesSummary:
    """Aggregated detection counts for one species."""

    species_name: str
    count: int
    best_confidence: float
    latest_analyzed_at: str


@dataclass(frozen=True)
class SpeciesStatistics:
    """Detailed statistics for one detected species."""

    species_name: str
    count: int
    recording_count: int
    best_confidence: float
    first_analyzed_at: str
    latest_analyzed_at: str
    hourly_counts: dict[int, int]
    daily_counts: dict[str, int]
    monthly_counts: dict[int, int]


@dataclass(frozen=True)
class DetectionOverview:
    """Overall detection statistics for a selected time window."""

    detection_count: int
    species_count: int
    recording_count: int
    first_analyzed_at: str | None
    latest_analyzed_at: str | None


def connect(database_path: Path) -> sqlite3.Connection:
    """Open the SQLite database and enable foreign keys."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create tables if they do not already exist."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audio_path TEXT NOT NULL UNIQUE,
            csv_path TEXT,
            duration_seconds REAL,
            analyzed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            species_name TEXT NOT NULL,
            scientific_name TEXT,
            common_name TEXT,
            confidence REAL NOT NULL,
            FOREIGN KEY (recording_id) REFERENCES recordings(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_detections_species_name
            ON detections(species_name);

        CREATE INDEX IF NOT EXISTS idx_detections_confidence
            ON detections(confidence);
        """
    )
    connection.commit()


def get_wav_duration_seconds(audio_path: Path) -> float | None:
    """Return WAV duration in seconds, or None if it cannot be read."""

    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except (wave.Error, OSError, ZeroDivisionError):
        return None


def split_species_name(species_name: str) -> tuple[str | None, str | None]:
    """Split BirdNET's 'Scientific name_Common name' value."""

    if "_" not in species_name:
        return species_name, None
    scientific_name, common_name = species_name.split("_", 1)
    return scientific_name, common_name


def save_analysis(
    database_path: Path,
    audio_path: Path,
    csv_path: Path,
    detections: list[object],
) -> int:
    """Store one analysis run and replace detections for the same audio file."""

    analyzed_at = datetime.now().isoformat(timespec="seconds")
    duration_seconds = get_wav_duration_seconds(audio_path)
    audio_path_text = str(audio_path.resolve())
    csv_path_text = str(csv_path.resolve())

    with connect(database_path) as connection:
        initialize_database(connection)
        cursor = connection.execute(
            """
            INSERT INTO recordings (
                audio_path,
                csv_path,
                duration_seconds,
                analyzed_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(audio_path) DO UPDATE SET
                csv_path = excluded.csv_path,
                duration_seconds = excluded.duration_seconds,
                analyzed_at = excluded.analyzed_at
            """,
            (audio_path_text, csv_path_text, duration_seconds, analyzed_at),
        )

        if cursor.lastrowid:
            recording_id = int(cursor.lastrowid)
        else:
            recording_id = int(
                connection.execute(
                    "SELECT id FROM recordings WHERE audio_path = ?",
                    (audio_path_text,),
                ).fetchone()[0]
            )

        connection.execute(
            "DELETE FROM detections WHERE recording_id = ?",
            (recording_id,),
        )

        for detection in detections:
            scientific_name, common_name = split_species_name(detection.species_name)
            connection.execute(
                """
                INSERT INTO detections (
                    recording_id,
                    start_time,
                    end_time,
                    species_name,
                    scientific_name,
                    common_name,
                    confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recording_id,
                    detection.start_time,
                    detection.end_time,
                    detection.species_name,
                    scientific_name,
                    common_name,
                    detection.confidence,
                ),
            )

        connection.commit()
        return recording_id


def get_recent_detections(
    database_path: Path,
    limit: int,
    min_confidence: float = 0.0,
) -> list[StoredDetection]:
    """Read the most recent detections from SQLite."""

    if limit < 1:
        raise ValueError("limit skal vaere mindst 1.")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence skal vaere mellem 0 og 1.")
    if not database_path.exists():
        return []

    with connect(database_path) as connection:
        initialize_database(connection)
        rows = connection.execute(
            """
            SELECT
                recordings.audio_path,
                detections.start_time,
                detections.end_time,
                detections.species_name,
                detections.confidence,
                recordings.analyzed_at
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE detections.confidence >= ?
            ORDER BY recordings.analyzed_at DESC, detections.confidence DESC
            LIMIT ?
            """,
            (min_confidence, limit),
        ).fetchall()

    return [
        StoredDetection(
            recording_path=str(row[0]),
            start_time=float(row[1]),
            end_time=float(row[2]),
            species_name=str(row[3]),
            confidence=float(row[4]),
            analyzed_at=str(row[5]),
        )
        for row in rows
    ]


def get_species_summary(
    database_path: Path,
    limit: int,
    min_confidence: float = 0.0,
    since_analyzed_at: str | None = None,
) -> list[SpeciesSummary]:
    """Read a compact per-species summary from SQLite."""

    if limit < 1:
        raise ValueError("limit skal vaere mindst 1.")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence skal vaere mellem 0 og 1.")
    if not database_path.exists():
        return []

    with connect(database_path) as connection:
        initialize_database(connection)
        where_clauses = ["detections.confidence >= ?"]
        params: list[object] = [min_confidence]
        if since_analyzed_at is not None:
            where_clauses.append("recordings.analyzed_at >= ?")
            params.append(since_analyzed_at)
        params.append(limit)

        rows = connection.execute(
            """
            SELECT
                detections.species_name,
                COUNT(*) AS detection_count,
                MAX(detections.confidence) AS best_confidence,
                MAX(recordings.analyzed_at) AS latest_analyzed_at
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + " AND ".join(where_clauses) + """
            GROUP BY detections.species_name
            ORDER BY detection_count DESC, best_confidence DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [
        SpeciesSummary(
            species_name=str(row[0]),
            count=int(row[1]),
            best_confidence=float(row[2]),
            latest_analyzed_at=str(row[3]),
        )
        for row in rows
    ]


def get_detection_overview(
    database_path: Path,
    min_confidence: float = 0.0,
    since_analyzed_at: str | None = None,
) -> DetectionOverview:
    """Read overall statistics for detections in a time window."""

    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence skal vaere mellem 0 og 1.")
    if not database_path.exists():
        return DetectionOverview(0, 0, 0, None, None)

    with connect(database_path) as connection:
        initialize_database(connection)
        where_clauses = ["detections.confidence >= ?"]
        params: list[object] = [min_confidence]
        if since_analyzed_at is not None:
            where_clauses.append("recordings.analyzed_at >= ?")
            params.append(since_analyzed_at)

        row = connection.execute(
            """
            SELECT
                COUNT(*) AS detection_count,
                COUNT(DISTINCT detections.species_name) AS species_count,
                COUNT(DISTINCT recordings.id) AS recording_count,
                MIN(recordings.analyzed_at) AS first_analyzed_at,
                MAX(recordings.analyzed_at) AS latest_analyzed_at
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + " AND ".join(where_clauses),
            params,
        ).fetchone()

    return DetectionOverview(
        detection_count=int(row[0] or 0),
        species_count=int(row[1] or 0),
        recording_count=int(row[2] or 0),
        first_analyzed_at=str(row[3]) if row[3] is not None else None,
        latest_analyzed_at=str(row[4]) if row[4] is not None else None,
    )


def get_species_statistics(
    database_path: Path,
    limit: int,
    min_confidence: float = 0.0,
    since_analyzed_at: str | None = None,
) -> list[SpeciesStatistics]:
    """Read per-species statistics including hourly and daily distributions."""

    if limit < 1:
        raise ValueError("limit skal vaere mindst 1.")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence skal vaere mellem 0 og 1.")
    if not database_path.exists():
        return []

    where_clauses = ["detections.confidence >= ?"]
    params: list[object] = [min_confidence]
    if since_analyzed_at is not None:
        where_clauses.append("recordings.analyzed_at >= ?")
        params.append(since_analyzed_at)
    where_sql = " AND ".join(where_clauses)

    with connect(database_path) as connection:
        initialize_database(connection)
        summary_rows = connection.execute(
            """
            SELECT
                detections.species_name,
                COUNT(*) AS detection_count,
                COUNT(DISTINCT recordings.id) AS recording_count,
                MAX(detections.confidence) AS best_confidence,
                MIN(recordings.analyzed_at) AS first_analyzed_at,
                MAX(recordings.analyzed_at) AS latest_analyzed_at
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + where_sql + """
            GROUP BY detections.species_name
            ORDER BY detection_count DESC, best_confidence DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

        species_names = [str(row[0]) for row in summary_rows]
        if not species_names:
            return []

        placeholders = ",".join("?" for _ in species_names)
        scoped_where_sql = f"{where_sql} AND detections.species_name IN ({placeholders})"
        scoped_params = [*params, *species_names]

        hourly_rows = connection.execute(
            """
            SELECT
                detections.species_name,
                CAST(substr(recordings.analyzed_at, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS detection_count
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + scoped_where_sql + """
            GROUP BY detections.species_name, hour
            """,
            scoped_params,
        ).fetchall()

        daily_rows = connection.execute(
            """
            SELECT
                detections.species_name,
                substr(recordings.analyzed_at, 1, 10) AS day,
                COUNT(*) AS detection_count
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + scoped_where_sql + """
            GROUP BY detections.species_name, day
            ORDER BY day
            """,
            scoped_params,
        ).fetchall()

        monthly_rows = connection.execute(
            """
            SELECT
                detections.species_name,
                CAST(substr(recordings.analyzed_at, 6, 2) AS INTEGER) AS month,
                COUNT(*) AS detection_count
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE """ + scoped_where_sql + """
            GROUP BY detections.species_name, month
            """,
            scoped_params,
        ).fetchall()

    hourly_by_species: dict[str, dict[int, int]] = {
        species_name: {} for species_name in species_names
    }
    for species_name, hour, count in hourly_rows:
        if hour is not None:
            hourly_by_species[str(species_name)][int(hour)] = int(count)

    daily_by_species: dict[str, dict[str, int]] = {
        species_name: {} for species_name in species_names
    }
    for species_name, day, count in daily_rows:
        daily_by_species[str(species_name)][str(day)] = int(count)

    monthly_by_species: dict[str, dict[int, int]] = {
        species_name: {} for species_name in species_names
    }
    for species_name, month, count in monthly_rows:
        if month is not None:
            monthly_by_species[str(species_name)][int(month)] = int(count)

    return [
        SpeciesStatistics(
            species_name=str(row[0]),
            count=int(row[1]),
            recording_count=int(row[2]),
            best_confidence=float(row[3]),
            first_analyzed_at=str(row[4]),
            latest_analyzed_at=str(row[5]),
            hourly_counts=hourly_by_species[str(row[0])],
            daily_counts=daily_by_species[str(row[0])],
            monthly_counts=monthly_by_species[str(row[0])],
        )
        for row in summary_rows
    ]


def get_species_statistics_for_name(
    database_path: Path,
    species_name: str,
    min_confidence: float = 0.0,
) -> SpeciesStatistics | None:
    """Read all-time statistics for one species."""

    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence skal vaere mellem 0 og 1.")
    if not database_path.exists():
        return None

    with connect(database_path) as connection:
        initialize_database(connection)
        summary_row = connection.execute(
            """
            SELECT
                detections.species_name,
                COUNT(*) AS detection_count,
                COUNT(DISTINCT recordings.id) AS recording_count,
                MAX(detections.confidence) AS best_confidence,
                MIN(recordings.analyzed_at) AS first_analyzed_at,
                MAX(recordings.analyzed_at) AS latest_analyzed_at
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE detections.confidence >= ? AND detections.species_name = ?
            GROUP BY detections.species_name
            """,
            (min_confidence, species_name),
        ).fetchone()

        if summary_row is None:
            return None

        hourly_rows = connection.execute(
            """
            SELECT
                CAST(substr(recordings.analyzed_at, 12, 2) AS INTEGER) AS hour,
                COUNT(*) AS detection_count
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE detections.confidence >= ? AND detections.species_name = ?
            GROUP BY hour
            """,
            (min_confidence, species_name),
        ).fetchall()

        monthly_rows = connection.execute(
            """
            SELECT
                CAST(substr(recordings.analyzed_at, 6, 2) AS INTEGER) AS month,
                COUNT(*) AS detection_count
            FROM detections
            JOIN recordings ON recordings.id = detections.recording_id
            WHERE detections.confidence >= ? AND detections.species_name = ?
            GROUP BY month
            """,
            (min_confidence, species_name),
        ).fetchall()

    return SpeciesStatistics(
        species_name=str(summary_row[0]),
        count=int(summary_row[1]),
        recording_count=int(summary_row[2]),
        best_confidence=float(summary_row[3]),
        first_analyzed_at=str(summary_row[4]),
        latest_analyzed_at=str(summary_row[5]),
        hourly_counts={
            int(hour): int(count)
            for hour, count in hourly_rows
            if hour is not None
        },
        daily_counts={},
        monthly_counts={
            int(month): int(count)
            for month, count in monthly_rows
            if month is not None
        },
    )
