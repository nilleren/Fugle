from __future__ import annotations

import argparse
import tomllib
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
from sounddevice import PortAudioError


DEFAULT_DURATION_SECONDS = 10
DEFAULT_CHANNELS = 1
MAX_RECORDINGS_TO_KEEP = 3
RECORDINGS_DIR = Path("recordings")
CONFIG_PATH = Path("config.toml")


@dataclass(frozen=True)
class Microphone:
    """A sounddevice input device that can record audio."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_samplerate: int


@dataclass(frozen=True)
class AudioConfig:
    """User-editable recording settings."""

    device: int | None
    duration_seconds: int
    sample_rate: int | None
    output_dir: Path


def clean_device_name(name: object) -> str:
    """Make Windows audio device names readable in the terminal."""

    return " ".join(str(name).split())


def load_config(path: Path) -> AudioConfig:
    """Load recording settings from a TOML file."""

    if not path.exists():
        return AudioConfig(
            device=None,
            duration_seconds=DEFAULT_DURATION_SECONDS,
            sample_rate=None,
            output_dir=RECORDINGS_DIR,
        )

    with path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    audio_config = raw_config.get("audio", {})
    if not isinstance(audio_config, dict):
        raise SystemExit(f"{path} skal indeholde en [audio]-sektion.")

    device = audio_config.get("device")
    duration_seconds = audio_config.get("duration_seconds", DEFAULT_DURATION_SECONDS)
    sample_rate = audio_config.get("sample_rate")
    output_dir = audio_config.get("output_dir", str(RECORDINGS_DIR))

    if device is not None and not isinstance(device, int):
        raise SystemExit("audio.device skal vaere et heltal.")
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        raise SystemExit("audio.duration_seconds skal vaere et heltal over 0.")
    if sample_rate is not None and (
        not isinstance(sample_rate, int) or sample_rate <= 0
    ):
        raise SystemExit("audio.sample_rate skal vaere et heltal over 0.")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise SystemExit("audio.output_dir skal vaere en mappe-sti.")

    return AudioConfig(
        device=device,
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        output_dir=Path(output_dir),
    )


def get_microphones() -> list[Microphone]:
    """Return all audio devices with at least one input channel."""

    devices = sd.query_devices()
    host_apis = sd.query_hostapis()
    microphones: list[Microphone] = []

    for index, device in enumerate(devices):
        max_input_channels = int(device.get("max_input_channels", 0))
        if max_input_channels < 1:
            continue

        host_api_index = int(device.get("hostapi", 0))
        host_api_name = str(host_apis[host_api_index]["name"])
        default_samplerate = int(device.get("default_samplerate") or 44100)

        microphones.append(
            Microphone(
                index=index,
                name=clean_device_name(device["name"]),
                host_api=host_api_name,
                max_input_channels=max_input_channels,
                default_samplerate=default_samplerate,
            )
        )

    return microphones


def print_microphones(microphones: list[Microphone]) -> None:
    """Print a small table of available microphones."""

    if not microphones:
        print("Ingen mikrofoner fundet.")
        return

    print("Tilgaengelige mikrofoner:")
    for microphone in microphones:
        print(
            f"[{microphone.index}] {microphone.name} "
            f"({microphone.host_api}, {microphone.max_input_channels} kanal(er), "
            f"{microphone.default_samplerate} Hz)"
        )


def choose_microphone(microphones: list[Microphone]) -> Microphone:
    """Ask the user to choose one of the available microphones."""

    microphone_by_index = {microphone.index: microphone for microphone in microphones}

    while True:
        raw_choice = input("Vaelg mikrofonnummer: ").strip()
        try:
            choice = int(raw_choice)
        except ValueError:
            print("Skriv et tal fra listen.")
            continue

        if choice in microphone_by_index:
            return microphone_by_index[choice]

        print("Det mikrofonnummer findes ikke i listen.")


def build_output_path(output_dir: Path) -> Path:
    """Create a timestamped WAV path inside output_dir."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_dir / f"recording-{timestamp}.wav"


def save_wav(path: Path, audio: np.ndarray, samplerate: int) -> None:
    """Save float audio from sounddevice as 16-bit PCM WAV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm_audio = (clipped * np.iinfo(np.int16).max).astype(np.int16)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(pcm_audio.shape[1])
        wav_file.setsampwidth(2)
        wav_file.setframerate(samplerate)
        wav_file.writeframes(pcm_audio.tobytes())


def cleanup_old_recordings(
    recordings_dir: Path,
    keep: int = MAX_RECORDINGS_TO_KEEP,
) -> list[Path]:
    """Delete old WAV recordings and keep the newest files."""

    if keep < 1:
        raise ValueError("keep skal vaere mindst 1.")

    wav_files = sorted(
        recordings_dir.glob("*.wav"),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    deleted_paths: list[Path] = []

    for old_path in wav_files[keep:]:
        old_path.unlink()
        deleted_paths.append(old_path)

    return deleted_paths


def record_audio(
    microphone: Microphone,
    duration_seconds: int,
    sample_rate: int,
    output_path: Path,
) -> None:
    """Record audio from the selected microphone and save it as WAV."""

    channels = min(DEFAULT_CHANNELS, microphone.max_input_channels)
    frames = int(duration_seconds * sample_rate)

    print(
        f"Optager {duration_seconds} sekunder fra '{microphone.name}' "
        f"ved {sample_rate} Hz..."
    )
    try:
        audio = sd.rec(
            frames,
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            device=microphone.index,
        )
        sd.wait()
    except PortAudioError as error:
        raise SystemExit(
            "Kunne ikke optage fra den valgte mikrofon. "
            "Proev et andet mikrofonnummer med --device eller ret config.toml. "
            f"Detalje: {error}"
        ) from error

    save_wav(output_path, audio, sample_rate)
    print(f"Gemte optagelse: {output_path}")

    deleted_paths = cleanup_old_recordings(output_path.parent)
    if deleted_paths:
        print(f"Slettede {len(deleted_paths)} gammel/gamle lydfil(er).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find mikrofoner og optag lyd til en WAV-fil."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Vis mikrofoner uden at optage.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Sti til konfigurationsfil. Standard: {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Antal sekunder der skal optages. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="Mikrofonnummer. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        help="Sample rate i Hz. Overstyrer config.toml.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Sti til WAV-fil. Hvis det udelades, bruges recordings/ med tidsstempel.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    microphones = get_microphones()
    print_microphones(microphones)

    if args.list:
        return

    if not microphones:
        raise SystemExit("Kan ikke optage, fordi ingen mikrofoner blev fundet.")

    duration_seconds = (
        args.duration if args.duration is not None else config.duration_seconds
    )
    if duration_seconds <= 0:
        raise SystemExit("--duration skal vaere mindst 1 sekund.")

    sample_rate = args.sample_rate if args.sample_rate is not None else config.sample_rate
    if sample_rate is not None and sample_rate <= 0:
        raise SystemExit("--sample-rate skal vaere mindst 1 Hz.")

    device = args.device if args.device is not None else config.device
    if device is None:
        microphone = choose_microphone(microphones)
    else:
        matching_microphones = [
            microphone for microphone in microphones if microphone.index == device
        ]
        if not matching_microphones:
            raise SystemExit(
                f"Mikrofonnummer {device} blev ikke fundet. "
                "Koer med --list for at se tilgaengelige mikrofoner."
            )
        microphone = matching_microphones[0]

    sample_rate = sample_rate or microphone.default_samplerate
    output_path = args.output or build_output_path(config.output_dir)
    record_audio(microphone, duration_seconds, sample_rate, output_path)


if __name__ == "__main__":
    main()
