from __future__ import annotations

import argparse
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path

from fuglestation.analyze_audio import DEFAULT_CONFIDENCE, DEFAULT_TOP_K
from fuglestation.record_audio import CONFIG_PATH
from fuglestation.run_cycle import CycleOptions, run_single_cycle
from fuglestation.station_status import (
    DEFAULT_STATUS_PATH,
    StationStatus,
    now_iso,
    write_status,
)


@dataclass(frozen=True)
class ScheduleConfig:
    """Timing rules for continuous station operation."""

    first_phase_seconds: int
    first_phase_interval_seconds: int
    second_phase_seconds: int
    second_phase_interval_seconds: int
    steady_interval_seconds: int
    quiet_start: clock_time
    quiet_end: clock_time


DEFAULT_SCHEDULE = ScheduleConfig(
    first_phase_seconds=120,
    first_phase_interval_seconds=30,
    second_phase_seconds=600,
    second_phase_interval_seconds=60,
    steady_interval_seconds=900,
    quiet_start=clock_time(22, 0),
    quiet_end=clock_time(5, 0),
)


def parse_clock_time(value: object) -> clock_time:
    """Parse HH:MM schedule values from config.toml."""

    if not isinstance(value, str):
        raise SystemExit("schedule quiet times skal skrives som tekst, fx \"22:00\".")

    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as error:
        raise SystemExit("schedule quiet times skal bruge formatet HH:MM.") from error

    return parsed.time()


def positive_int(value: object, name: str) -> int:
    """Validate a positive integer config value."""

    if not isinstance(value, int) or value <= 0:
        raise SystemExit(f"schedule.{name} skal vaere et heltal over 0.")
    return value


def load_schedule_config(path: Path) -> ScheduleConfig:
    """Load scheduler settings from config.toml."""

    if not path.exists():
        return DEFAULT_SCHEDULE

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    schedule = raw_config.get("schedule", {})
    if not isinstance(schedule, dict):
        raise SystemExit(f"{path} skal indeholde en [schedule]-sektion.")

    return ScheduleConfig(
        first_phase_seconds=positive_int(
            schedule.get("first_phase_seconds", DEFAULT_SCHEDULE.first_phase_seconds),
            "first_phase_seconds",
        ),
        first_phase_interval_seconds=positive_int(
            schedule.get(
                "first_phase_interval_seconds",
                DEFAULT_SCHEDULE.first_phase_interval_seconds,
            ),
            "first_phase_interval_seconds",
        ),
        second_phase_seconds=positive_int(
            schedule.get("second_phase_seconds", DEFAULT_SCHEDULE.second_phase_seconds),
            "second_phase_seconds",
        ),
        second_phase_interval_seconds=positive_int(
            schedule.get(
                "second_phase_interval_seconds",
                DEFAULT_SCHEDULE.second_phase_interval_seconds,
            ),
            "second_phase_interval_seconds",
        ),
        steady_interval_seconds=positive_int(
            schedule.get(
                "steady_interval_seconds",
                DEFAULT_SCHEDULE.steady_interval_seconds,
            ),
            "steady_interval_seconds",
        ),
        quiet_start=parse_clock_time(
            schedule.get("quiet_start", DEFAULT_SCHEDULE.quiet_start.strftime("%H:%M"))
        ),
        quiet_end=parse_clock_time(
            schedule.get("quiet_end", DEFAULT_SCHEDULE.quiet_end.strftime("%H:%M"))
        ),
    )


def interval_for_elapsed(elapsed_seconds: float, schedule: ScheduleConfig) -> int:
    """Return delay before next cycle for the current scheduler age."""

    if elapsed_seconds < schedule.first_phase_seconds:
        return schedule.first_phase_interval_seconds
    if elapsed_seconds < schedule.first_phase_seconds + schedule.second_phase_seconds:
        return schedule.second_phase_interval_seconds
    return schedule.steady_interval_seconds


def is_quiet_time(now: datetime, schedule: ScheduleConfig) -> bool:
    """Return whether now is inside the configured no-recording window."""

    current = now.time()
    if schedule.quiet_start < schedule.quiet_end:
        return schedule.quiet_start <= current < schedule.quiet_end
    return current >= schedule.quiet_start or current < schedule.quiet_end


def next_quiet_end(now: datetime, schedule: ScheduleConfig) -> datetime:
    """Return the next datetime where recording is allowed."""

    quiet_end_today = datetime.combine(now.date(), schedule.quiet_end)
    if now.time() < schedule.quiet_end:
        return quiet_end_today
    return quiet_end_today + timedelta(days=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Koer fuglestationen kontinuerligt efter en tidsplan."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Sti til konfigurationsfil. Standard: {CONFIG_PATH}.",
    )
    parser.add_argument("--device", type=int, help="Mikrofonnummer. Overstyrer config.")
    parser.add_argument("--duration", type=int, help="Optagelaengde. Overstyrer config.")
    parser.add_argument("--sample-rate", type=int, help="Sample rate. Overstyrer config.")
    parser.add_argument(
        "--confidence",
        type=float,
        help=(
            "Minimum confidence for BirdNET-detektioner. "
            f"Standard laeses fra config.toml eller {DEFAULT_CONFIDENCE}."
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Antal bedste arter per segment. Standard: {DEFAULT_TOP_K}.",
    )
    parser.add_argument("--no-geo", action="store_true", help="Sluk geo-filter.")
    parser.add_argument("--no-db", action="store_true", help="Gem ikke i SQLite.")
    parser.add_argument(
        "--ignore-quiet-hours",
        action="store_true",
        help="Ignorer natpause. Nyttigt til test.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        help="Stop efter dette antal cyklusser. Nyttigt til test.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Vis planlagte cyklusser uden at optage eller analysere.",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=DEFAULT_STATUS_PATH,
        help=f"Sti til statusfil. Standard: {DEFAULT_STATUS_PATH}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_cycles is not None and args.max_cycles < 1:
        raise SystemExit("--max-cycles skal vaere mindst 1.")

    schedule = load_schedule_config(args.config)
    started_at = datetime.now()
    cycles_run = 0
    last_cycle_started_at = None
    last_cycle_finished_at = None

    print("Starter kontinuerlig fuglestation.")
    print(
        "Plan: 30 sek. interval i 2 min., 60 sek. interval i 10 min., "
        "derefter 15 min. interval."
    )
    print(
        f"Natpause: {schedule.quiet_start.strftime('%H:%M')}-"
        f"{schedule.quiet_end.strftime('%H:%M')}."
    )
    write_status(
        args.status_file,
        StationStatus(
            state="starting",
            message="Scheduler starter.",
            updated_at=now_iso(),
        ),
    )

    try:
        while True:
            now = datetime.now()
            if not args.ignore_quiet_hours and is_quiet_time(now, schedule):
                resume_at = next_quiet_end(now, schedule)
                sleep_seconds = max(1, int((resume_at - now).total_seconds()))
                print(
                    f"Natpause aktiv. Næste mulige optagelse: "
                    f"{resume_at.strftime('%Y-%m-%d %H:%M')}."
                )
                write_status(
                    args.status_file,
                    StationStatus(
                        state="quiet",
                        message="Natpause aktiv.",
                        updated_at=now_iso(),
                        cycles_run=cycles_run,
                        last_cycle_started_at=last_cycle_started_at,
                        last_cycle_finished_at=last_cycle_finished_at,
                        next_cycle_at=resume_at.isoformat(timespec="seconds"),
                    ),
                )
                if args.dry_run or args.max_cycles is not None:
                    print("Stopper uden at vente, fordi dette er en testkørsel.")
                    write_status(
                        args.status_file,
                        StationStatus(
                            state="stopped",
                            message="Testkørsel stoppede under natpause.",
                            updated_at=now_iso(),
                            cycles_run=cycles_run,
                            last_cycle_started_at=last_cycle_started_at,
                            last_cycle_finished_at=last_cycle_finished_at,
                            next_cycle_at=resume_at.isoformat(timespec="seconds"),
                        ),
                    )
                    return
                time.sleep(sleep_seconds)
                continue

            cycles_run += 1
            last_cycle_started_at = now.isoformat(timespec="seconds")
            print(f"Starter cyklus {cycles_run} kl. {now.strftime('%H:%M:%S')}.")
            write_status(
                args.status_file,
                StationStatus(
                    state="running_cycle",
                    message=f"Kører cyklus {cycles_run}.",
                    updated_at=now_iso(),
                    cycles_run=cycles_run,
                    last_cycle_started_at=last_cycle_started_at,
                    last_cycle_finished_at=last_cycle_finished_at,
                ),
            )
            try:
                if args.dry_run:
                    print("Dry-run: springer optagelse og analyse over.")
                else:
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
            except Exception as error:
                write_status(
                    args.status_file,
                    StationStatus(
                        state="error",
                        message="Sidste cyklus fejlede.",
                        updated_at=now_iso(),
                        cycles_run=cycles_run,
                        last_cycle_started_at=last_cycle_started_at,
                        last_cycle_finished_at=last_cycle_finished_at,
                        last_error=str(error),
                    ),
                )
                raise

            last_cycle_finished_at = datetime.now().isoformat(timespec="seconds")

            if args.max_cycles is not None and cycles_run >= args.max_cycles:
                print("Max cycles nået. Stopper scheduler.")
                write_status(
                    args.status_file,
                    StationStatus(
                        state="stopped",
                        message="Max cycles nået.",
                        updated_at=now_iso(),
                        cycles_run=cycles_run,
                        last_cycle_started_at=last_cycle_started_at,
                        last_cycle_finished_at=last_cycle_finished_at,
                    ),
                )
                return

            elapsed_seconds = (datetime.now() - started_at).total_seconds()
            sleep_seconds = interval_for_elapsed(elapsed_seconds, schedule)
            next_cycle_at = datetime.now() + timedelta(seconds=sleep_seconds)
            print(f"Venter {sleep_seconds} sekunder før næste cyklus.")
            write_status(
                args.status_file,
                StationStatus(
                    state="waiting",
                    message="Venter på næste cyklus.",
                    updated_at=now_iso(),
                    cycles_run=cycles_run,
                    last_cycle_started_at=last_cycle_started_at,
                    last_cycle_finished_at=last_cycle_finished_at,
                    next_cycle_at=next_cycle_at.isoformat(timespec="seconds"),
                ),
            )
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        write_status(
            args.status_file,
            StationStatus(
                state="stopped",
                message="Scheduler stoppet af bruger.",
                updated_at=now_iso(),
                cycles_run=cycles_run,
                last_cycle_started_at=last_cycle_started_at,
                last_cycle_finished_at=last_cycle_finished_at,
            ),
        )
        print("Scheduler stoppet af bruger.")


if __name__ == "__main__":
    main()
