from __future__ import annotations

import argparse
import json
import os
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.ebird.org/v2"
DEFAULT_ENV_PATH = Path(".env")
EBIRD_API_KEY_ENV = "EBIRD_API_KEY"


def create_ssl_context() -> ssl.SSLContext | None:
    """Use certifi's certificate bundle when it is available."""

    try:
        import certifi
    except ImportError:
        return None

    return ssl.create_default_context(cafile=certifi.where())


def load_env_file(path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Load simple KEY=VALUE lines from .env without adding a dependency."""

    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def get_api_key() -> str:
    """Read the eBird API key from environment or local .env file."""

    env_values = load_env_file()
    api_key = os.environ.get(EBIRD_API_KEY_ENV) or env_values.get(EBIRD_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{EBIRD_API_KEY_ENV} mangler. Opret .env med {EBIRD_API_KEY_ENV}=..."
        )
    return api_key


def request_json(
    endpoint: str,
    params: dict[str, object] | None = None,
    timeout_seconds: int = 15,
) -> object:
    """Call an eBird API endpoint and return decoded JSON."""

    query = f"?{urlencode(params)}" if params else ""
    url = f"{BASE_URL}{endpoint}{query}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-eBirdApiToken": get_api_key(),
        },
    )

    try:
        with urlopen(
            request, timeout=timeout_seconds, context=create_ssl_context()
        ) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        raise RuntimeError(f"eBird svarede med HTTP {error.code}.") from error
    except URLError as error:
        raise RuntimeError(f"Kunne ikke kontakte eBird: {error.reason}") from error

    return json.loads(body)


def get_recent_observations(
    region_code: str = "DK",
    back_days: int = 7,
    max_results: int = 5,
) -> list[dict[str, object]]:
    """Return recent observations for a region."""

    data = request_json(
        f"/data/obs/{region_code}/recent",
        {
            "back": back_days,
            "maxResults": max_results,
        },
    )
    if not isinstance(data, list):
        raise RuntimeError("eBird returnerede ikke en liste.")
    return [item for item in data if isinstance(item, dict)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test forbindelse til eBird API.")
    parser.add_argument(
        "--region",
        default="DK",
        help="eBird region code. Standard: DK.",
    )
    parser.add_argument(
        "--back-days",
        type=int,
        default=7,
        help="Antal dage tilbage. Standard: 7.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Maksimalt antal observationer. Standard: 5.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    observations = get_recent_observations(
        region_code=args.region,
        back_days=args.back_days,
        max_results=args.max_results,
    )

    print(f"eBird svarer. Hentede {len(observations)} observation(er).")
    for observation in observations:
        common_name = observation.get("comName", "ukendt art")
        location = observation.get("locName", "ukendt sted")
        date = observation.get("obsDt", "ukendt tidspunkt")
        print(f"- {common_name} | {location} | {date}")


if __name__ == "__main__":
    main()
