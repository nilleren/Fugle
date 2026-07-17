from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median


DEFAULT_BIRD_DIR = Path("assets/birds")


def has_transparency(image: object) -> bool:
    """Return True if a Pillow image already has transparent pixels."""

    if getattr(image, "mode", None) != "RGBA":
        return False
    alpha = image.getchannel("A")
    return alpha.getextrema()[0] == 0


def crop_to_alpha(image: object, margin: float) -> object:
    """Crop an RGBA image to its visible alpha bounds with a small margin."""

    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        return image

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    padding = round(margin * max(width, height))
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(image.width, bbox[2] + padding)
    bottom = min(image.height, bbox[3] + padding)
    return image.crop((left, top, right, bottom))


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    """Return squared RGB distance between two colors."""

    return sum((left[index] - right[index]) ** 2 for index in range(3))


def edge_background_color(image: object) -> tuple[int, int, int]:
    """Estimate a flat background color from transparent image edges."""

    rgb_samples = []
    pixels = image.load()
    for x in range(image.width):
        rgb_samples.append(pixels[x, 0][:3])
        rgb_samples.append(pixels[x, image.height - 1][:3])
    for y in range(image.height):
        rgb_samples.append(pixels[0, y][:3])
        rgb_samples.append(pixels[image.width - 1, y][:3])

    return tuple(round(median(channel)) for channel in zip(*rgb_samples))


def remove_connected_flat_background(image: object, tolerance: int) -> object:
    """Make edge-connected flat background pixels transparent."""

    image = image.convert("RGBA")
    pixels = image.load()
    background = edge_background_color(image)
    threshold = tolerance * tolerance
    stack: list[tuple[int, int]] = []
    seen = set()

    def should_remove(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        if alpha == 0:
            return False
        return color_distance((red, green, blue), background) <= threshold

    for x in range(image.width):
        if should_remove(x, 0):
            stack.append((x, 0))
        if should_remove(x, image.height - 1):
            stack.append((x, image.height - 1))
    for y in range(image.height):
        if should_remove(0, y):
            stack.append((0, y))
        if should_remove(image.width - 1, y):
            stack.append((image.width - 1, y))

    while stack:
        x, y = stack.pop()
        if (x, y) in seen:
            continue
        seen.add((x, y))
        if not should_remove(x, y):
            continue

        red, green, blue, _alpha = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
        if x > 0:
            stack.append((x - 1, y))
        if x < image.width - 1:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y < image.height - 1:
            stack.append((x, y + 1))

    return image


def collect_paths(directory: Path, filenames: list[str]) -> list[Path]:
    """Return image paths to process."""

    if filenames:
        paths = [directory / filename for filename in filenames]
        missing = [path for path in paths if not path.exists()]
        if missing:
            names = ", ".join(path.name for path in missing)
            raise SystemExit(f"Fandt ikke billedfil: {names}")
        return paths

    paths = sorted(directory.glob("*.png"))
    if not paths:
        raise SystemExit(f"Fandt ingen PNG-filer i {directory}.")
    return paths


def cutout_images(
    paths: list[Path],
    model: str,
    margin: float,
    flat_background_tolerance: int,
    force: bool,
) -> tuple[int, int]:
    """Remove image backgrounds and save transparent PNG files in place."""

    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as error:
        raise SystemExit(
            "Mangler billed-afhaengigheder. Installer dem med: "
            "python -m pip install -r requirements-cutout.txt"
        ) from error

    session = new_session(model)
    done = 0
    skipped = 0

    for path in paths:
        with Image.open(path) as source:
            if not force and has_transparency(source):
                print(f"Springer over, har allerede transparens: {path.name}")
                skipped += 1
                continue

            cutout = remove(source.convert("RGB"), session=session)
            cutout = remove_connected_flat_background(
                cutout,
                flat_background_tolerance,
            )
            cutout = crop_to_alpha(cutout, margin)
            cutout.save(path)

        print(f"Klip: {path.name} -> {cutout.width}x{cutout.height}")
        done += 1

    return done, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fjern baggrunden fra fuglebilleder i assets/birds."
    )
    parser.add_argument(
        "filenames",
        nargs="*",
        help="Bestemte PNG-filnavne i assets/birds. Udelades de, behandles alle.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_BIRD_DIR,
        help="Mappe med fuglebilleder. Standard: assets/birds.",
    )
    parser.add_argument(
        "--model",
        default="birefnet-general",
        help="rembg-model. Standard: birefnet-general.",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.02,
        help="Luft omkring fuglen efter beskaering. Standard: 0.02.",
    )
    parser.add_argument(
        "--flat-background-tolerance",
        type=int,
        default=32,
        help="Farvetolerance til flad kantbaggrund. Standard: 32.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Behandl ogsaa billeder, der allerede har transparent baggrund.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = collect_paths(args.dir, args.filenames)
    done, skipped = cutout_images(
        paths=paths,
        model=args.model,
        margin=args.margin,
        flat_background_tolerance=args.flat_background_tolerance,
        force=args.force,
    )
    print(f"Faerdig: {done} klippet, {skipped} sprunget over.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
