from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


DEFAULT_STATUS_PATH = Path("data/status.json")


@dataclass(frozen=True)
class StationStatus:
    """Current runtime status for the local station UI."""

    state: str
    message: str
    updated_at: str
    cycles_run: int = 0
    last_cycle_started_at: str | None = None
    last_cycle_finished_at: str | None = None
    next_cycle_at: str | None = None
    last_error: str | None = None


def now_iso() -> str:
    """Return local time as a compact ISO timestamp."""

    return datetime.now().isoformat(timespec="seconds")


def default_status() -> StationStatus:
    """Return a status for a station that has not written state yet."""

    return StationStatus(
        state="unknown",
        message="Ingen scheduler-status endnu.",
        updated_at=now_iso(),
    )


def write_status(path: Path, status: StationStatus) -> None:
    """Atomically write station status as JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(status)

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        newline="\n",
    ) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=True, indent=2)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)

    temp_path.replace(path)


def read_status(path: Path) -> StationStatus:
    """Read station status from JSON, or return a default status."""

    if not path.exists():
        return default_status()

    try:
        with path.open("r", encoding="utf-8") as status_file:
            payload = json.load(status_file)
    except (OSError, json.JSONDecodeError):
        return StationStatus(
            state="error",
            message="Kunne ikke laese scheduler-status.",
            updated_at=now_iso(),
        )

    return StationStatus(
        state=str(payload.get("state", "unknown")),
        message=str(payload.get("message", "Ingen statusbesked.")),
        updated_at=str(payload.get("updated_at", now_iso())),
        cycles_run=int(payload.get("cycles_run", 0)),
        last_cycle_started_at=payload.get("last_cycle_started_at"),
        last_cycle_finished_at=payload.get("last_cycle_finished_at"),
        next_cycle_at=payload.get("next_cycle_at"),
        last_error=payload.get("last_error"),
    )
