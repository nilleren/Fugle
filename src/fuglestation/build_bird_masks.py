from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path


DEFAULT_BIRD_DIR = Path("assets/birds")
DEFAULT_OUTPUT_PATH = DEFAULT_BIRD_DIR / "masks.json"
MASK_MAX = 88
ALPHA_ON = 127


def pack_alpha_mask(image: object) -> dict[str, object]:
    """Return a compact 1-bit alpha mask for a Pillow RGBA image."""

    width, height = image.size
    scale = MASK_MAX / max(width, height)
    mask_width = max(1, round(width * scale))
    mask_height = max(1, round(height * scale))
    alpha = image.getchannel("A").resize((mask_width, mask_height))
    pixels = alpha.load()
    bits = bytearray((mask_width * mask_height + 7) // 8)

    for y in range(mask_height):
        for x in range(mask_width):
            if pixels[x, y] > ALPHA_ON:
                index = y * mask_width + x
                bits[index >> 3] |= 1 << (7 - (index & 7))

    return {
        "w": mask_width,
        "h": mask_height,
        "bits": base64.b64encode(bytes(bits)).decode("ascii"),
    }


def build_masks(bird_dir: Path) -> dict[str, dict[str, object]]:
    """Build masks keyed by image stem."""

    try:
        from PIL import Image
    except ImportError as error:
        raise SystemExit(
            "Mangler Pillow. Installer billed-afhaengigheder med: "
            "python -m pip install -r requirements-cutout.txt"
        ) from error

    masks: dict[str, dict[str, object]] = {}
    for path in sorted(bird_dir.glob("*.png")):
        with Image.open(path) as image:
            masks[path.stem] = pack_alpha_mask(image.convert("RGBA"))

    if not masks:
        raise SystemExit(f"Fandt ingen PNG-filer i {bird_dir}.")
    return masks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Byg kompakte alpha-masker til vaegvisningens fuglepakning."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_BIRD_DIR,
        help="Mappe med fuglebilleder. Standard: assets/birds.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="JSON-fil der skal skrives. Standard: assets/birds/masks.json.",
    )
    args = parser.parse_args()

    masks = build_masks(args.dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(masks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Skrev {len(masks)} masker til {args.output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
