from __future__ import annotations

import base64
import json
import math
from datetime import datetime
from io import BytesIO
from pathlib import Path
from textwrap import shorten

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


# A light warm ivory gives the six-color Spectra panel a mostly white base
# with a restrained yellow dither, keeping dark and colored birds distinct.
BACKGROUND = (251, 242, 214)
TEXT = (43, 33, 23)
MUTED = (88, 70, 51)
LINE = (178, 145, 93)
SPECTRA_PALETTE = [
    (0, 0, 0),
    (255, 255, 255),
    (255, 213, 0),
    (220, 35, 35),
    (0, 145, 75),
    (20, 72, 155),
]
PORTRAIT_SIZE = (1200, 1600)
LANDSCAPE_SIZE = (1600, 1200)
GRID_STRIDE = 4
COLLAGE_PAD_CELLS = 4
LABEL_TOP_GAP_CELLS = 2
TERRACE_BACKGROUND = (234, 210, 159)
TERRACE_EINK_BACKGROUND = (240, 216, 170)
TERRACE_FALLBACK_SLOTS = [
    {"x": 0.18, "y": 0.19, "size": 0.15, "placementType": "airborne"},
    {"x": 0.40, "y": 0.13, "size": 0.14, "placementType": "airborne"},
    {"x": 0.65, "y": 0.20, "size": 0.14, "placementType": "airborne"},
    {"x": 0.82, "y": 0.34, "size": 0.13, "placementType": "airborne"},
    {"x": 0.18, "y": 0.42, "size": 0.14, "placementType": "perched"},
    {"x": 0.45, "y": 0.37, "size": 0.13, "placementType": "airborne"},
    {"x": 0.69, "y": 0.43, "size": 0.13, "placementType": "perched"},
    {"x": 0.84, "y": 0.57, "size": 0.12, "placementType": "perched"},
    {"x": 0.12, "y": 0.66, "size": 0.15, "placementType": "ground"},
    {"x": 0.30, "y": 0.76, "size": 0.14, "placementType": "ground"},
    {"x": 0.55, "y": 0.72, "size": 0.13, "placementType": "perched"},
    {"x": 0.76, "y": 0.80, "size": 0.13, "placementType": "ground"},
]


def parse_hex_color(
    value: object,
    fallback: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Convert a #RRGGBB setting to an RGB tuple, falling back safely."""

    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return fallback
    try:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
    except ValueError:
        return fallback


def split_display_name(display_name: str) -> tuple[str, str]:
    """Split a display name into Danish and Latin parts."""

    parts = [part.strip() for part in display_name.split("/") if part.strip()]
    if len(parts) < 2:
        return display_name.strip(), ""
    return parts[0], " / ".join(parts[1:])


def format_seen_time(value: str | None, now: datetime | None = None) -> str:
    """Return a short Danish relative time for the latest heard species."""

    if not value:
        return "ukendt tidspunkt"

    now = now or datetime.now()
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return "ukendt tidspunkt"

    seconds = max(0, round((now - date).total_seconds()))
    if seconds < 90:
        return "lige nu"

    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} min. siden"

    hours = round(minutes / 60)
    if hours < 36:
        return f"{hours} t. siden"

    return date.strftime("%d.%m.%Y")


def latest_species(species: list[dict[str, object]]) -> dict[str, object] | None:
    """Choose the same latest species tie-break as the wall view."""

    if not species:
        return None

    def sort_key(item: dict[str, object]) -> tuple[str, int]:
        analyzed_at = str(item.get("latest_analyzed_at", ""))
        count = int(item.get("count", 0) or 0)
        return analyzed_at, -count

    return max(species, key=sort_key)


def load_font(size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a serif-ish font available on Windows and Raspberry Pi."""

    candidates = [
        "C:/Windows/Fonts/BOD_R.TTF",
        "C:/Windows/Fonts/BOD_B.TTF" if bold else "",
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/georgiab.ttf" if bold else "",
        "C:/Windows/Fonts/georgiai.ttf" if italic else "",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf" if italic else "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)

    try:
        return ImageFont.truetype("DejaVuSerif.ttf", size)
    except OSError:
        return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Measure rendered text."""

    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = TEXT,
) -> None:
    """Draw one line centered inside a box."""

    width, height = text_size(draw, text, font)
    left, top, right, bottom = box
    x = left + ((right - left) - width) / 2
    y = top + ((bottom - top) - height) / 2
    draw.text((x, y), text, font=font, fill=fill)


def fit_text_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    initial_size: int,
    min_size: int = 12,
) -> ImageFont.ImageFont:
    """Return a font size that fits a single line."""

    size = initial_size
    while size > min_size:
        font = load_font(size)
        width, _ = text_size(draw, text, font)
        if width <= max_width:
            return font
        size -= 1
    return load_font(min_size)


def open_bird_image(image_path: Path, max_size: tuple[int, int]) -> Image.Image | None:
    """Open and fit one transparent bird image."""

    if not image_path.exists():
        return None

    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
    except OSError:
        return None

    return ImageOps.contain(image, max_size, Image.Resampling.LANCZOS)


def species_seed(text: str) -> int:
    """Return the same stable seed style as the browser wall view."""

    seed = 0
    for char in text:
        seed = (seed * 31 + ord(char)) % 9973
    return seed


def image_stem(item: dict[str, object]) -> str:
    """Return the mask key for a species payload."""

    filename = str(item.get("image_filename") or "")
    return Path(filename).stem


def load_bird_masks(bird_assets_dir: Path) -> dict[str, dict[str, object]]:
    """Load packed alpha masks generated for the browser wall view."""

    masks_path = bird_assets_dir / "masks.json"
    if not masks_path.exists():
        return {}

    try:
        raw_masks = json.loads(masks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw_masks, dict):
        return {}

    return {
        str(key): value
        for key, value in raw_masks.items()
        if isinstance(value, dict)
    }


def decode_mask(mask_record: dict[str, object] | None) -> dict[str, object] | None:
    """Decode one compact alpha mask into occupied cells."""

    if not mask_record:
        return None
    if "cells" in mask_record:
        return mask_record

    try:
        width = int(mask_record["w"])
        height = int(mask_record["h"])
        bits = base64.b64decode(str(mask_record["bits"]))
    except (KeyError, TypeError, ValueError):
        return None

    cells: list[tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            byte = bits[index >> 3]
            if (byte >> (7 - (index & 7))) & 1:
                cells.append((x, y))

    mask_record["cells"] = cells
    return mask_record


def species_size_score(item: dict[str, object], size_mode: str, max_count: int) -> float:
    """Return the same relative size score as the browser wall view."""

    count = int(item.get("count", 0) or 0)
    if size_mode == "equal":
        return 1.0
    if size_mode == "rare":
        return math.pow(max(1, max_count - count + 1), 0.62)
    return math.pow(max(1, count), 0.62)


def make_packed_tiles(
    species: list[dict[str, object]],
    masks: dict[str, dict[str, object]],
    width: int,
    height: int,
    show_names: bool,
    size_mode: str,
) -> list[dict[str, object]]:
    """Create tile records with image sizes based on mask aspect ratios."""

    max_count = max((int(item.get("count", 0) or 0) for item in species), default=1)
    viewport_area = width * height
    budget = viewport_area * (0.5 if len(species) <= 4 else 0.42)
    min_area = viewport_area * 0.012
    scored: list[dict[str, object]] = []

    for index, item in enumerate(species):
        mask = decode_mask(masks.get(image_stem(item)))
        if mask is None or not item.get("image_filename"):
            return []
        score = species_size_score(item, size_mode, max_count)
        mask_width = int(mask["w"])
        mask_height = int(mask["h"])
        seed = species_seed(str(item.get("species_name") or item.get("display_name") or ""))
        scored.append(
            {
                "species": item,
                "index": index,
                "mask": mask,
                "score": score,
                "aspect_ratio": mask_width / mask_height,
                "rotate": ((seed % 9) - 4) * 0.7,
            }
        )

    score_sum = sum(float(tile["score"]) for tile in scored) or 1.0
    label_height = round(min(width, height) * 0.045) if show_names else 0
    tiles: list[dict[str, object]] = []
    for tile in scored:
        aspect_ratio = float(tile["aspect_ratio"])
        area = max(min_area, (budget * float(tile["score"])) / score_sum)
        image_width = math.sqrt(area * aspect_ratio)
        image_height = image_width / aspect_ratio
        tiles.append(
            {
                **tile,
                "image_width": image_width,
                "image_height": image_height,
                "full_width": image_width,
                "full_height": image_height + label_height,
                "label_height": label_height,
            }
        )
    return tiles


def pack_tiles(
    tiles: list[dict[str, object]],
    width: int,
    height: int,
) -> list[dict[str, object]]:
    """Pack tiles by alpha mask, following the browser wall algorithm."""

    grid_width = math.ceil(width / GRID_STRIDE) + 2
    grid_height = math.ceil(height / GRID_STRIDE) + 2
    grid = bytearray(grid_width * grid_height)
    center_x = width / 2
    center_y = height / 2
    placed: list[dict[str, object]] = []

    def cell_range(tile: dict[str, object], x: float, y: float, cell: tuple[int, int]) -> tuple[int, int, int, int]:
        mask = tile["mask"]
        scale_x = float(tile["image_width"]) / int(mask["w"])
        scale_y = float(tile["image_height"]) / int(mask["h"])
        x0 = max(0, math.floor((x + cell[0] * scale_x) / GRID_STRIDE))
        y0 = max(0, math.floor((y + cell[1] * scale_y) / GRID_STRIDE))
        x1 = min(grid_width - 1, math.floor((x + (cell[0] + 1) * scale_x) / GRID_STRIDE))
        y1 = min(grid_height - 1, math.floor((y + (cell[1] + 1) * scale_y) / GRID_STRIDE))
        return x0, y0, x1, y1

    def collides(tile: dict[str, object], x: float, y: float) -> bool:
        if (
            x < 0
            or y < 0
            or x + float(tile["full_width"]) > width
            or y + float(tile["full_height"]) > height
        ):
            return True

        mask = tile["mask"]
        for cell in mask["cells"]:
            x0, y0, x1, y1 = cell_range(tile, x, y, cell)
            for gy in range(y0, y1 + 1):
                offset = gy * grid_width
                for gx in range(x0, x1 + 1):
                    if grid[offset + gx]:
                        return True

        if float(tile["label_height"]) > 0:
            label_y0 = max(
                0,
                math.floor((y + float(tile["image_height"])) / GRID_STRIDE)
                - LABEL_TOP_GAP_CELLS,
            )
            label_y1 = min(
                grid_height - 1,
                math.floor((y + float(tile["full_height"])) / GRID_STRIDE),
            )
            label_x0 = max(0, math.floor(x / GRID_STRIDE))
            label_x1 = min(
                grid_width - 1,
                math.floor((x + float(tile["full_width"])) / GRID_STRIDE),
            )
            for gy in range(label_y0, label_y1 + 1):
                offset = gy * grid_width
                for gx in range(label_x0, label_x1 + 1):
                    if grid[offset + gx]:
                        return True

        return False

    def stamp(tile: dict[str, object], x: float, y: float) -> None:
        mask = tile["mask"]
        for cell in mask["cells"]:
            x0, y0, x1, y1 = cell_range(tile, x, y, cell)
            for gy in range(max(0, y0 - COLLAGE_PAD_CELLS), min(grid_height - 1, y1 + COLLAGE_PAD_CELLS) + 1):
                offset = gy * grid_width
                for gx in range(max(0, x0 - COLLAGE_PAD_CELLS), min(grid_width - 1, x1 + COLLAGE_PAD_CELLS) + 1):
                    grid[offset + gx] = 1

        if float(tile["label_height"]) > 0:
            label_y0 = max(
                0,
                math.floor((y + float(tile["image_height"])) / GRID_STRIDE)
                - COLLAGE_PAD_CELLS
                - LABEL_TOP_GAP_CELLS,
            )
            label_y1 = min(
                grid_height - 1,
                math.floor((y + float(tile["full_height"])) / GRID_STRIDE)
                + COLLAGE_PAD_CELLS,
            )
            label_x0 = max(0, math.floor(x / GRID_STRIDE) - COLLAGE_PAD_CELLS)
            label_x1 = min(
                grid_width - 1,
                math.floor((x + float(tile["full_width"])) / GRID_STRIDE)
                + COLLAGE_PAD_CELLS,
            )
            for gy in range(label_y0, label_y1 + 1):
                offset = gy * grid_width
                for gx in range(label_x0, label_x1 + 1):
                    grid[offset + gx] = 1

    for index, tile in enumerate(
        sorted(
            tiles,
            key=lambda value: float(value["image_width"]) * float(value["image_height"]),
            reverse=True,
        )
    ):
        best: tuple[float, float] | None = None
        best_cost = math.inf
        step = max(
            GRID_STRIDE,
            min(float(tile["image_width"]), float(tile["image_height"])) * 0.08,
        )
        max_radius = max(width, height)

        if index == 0:
            best = (
                center_x - float(tile["full_width"]) / 2,
                center_y - float(tile["full_height"]) / 2,
            )
        else:
            total = {"x": 0.0, "y": 0.0, "area": 0.0}
            for placed_tile in placed:
                area = float(placed_tile["image_width"]) * float(placed_tile["image_height"])
                total["x"] += (float(placed_tile["x"]) + float(placed_tile["full_width"]) / 2) * area
                total["y"] += (float(placed_tile["y"]) + float(placed_tile["full_height"]) / 2) * area
                total["area"] += area
            target_x = total["x"] / total["area"]
            target_y = total["y"] / total["area"]

            radius = 0.0
            while radius <= max_radius and best is None:
                samples = max(24, math.floor(radius / 2))
                for sample in range(samples):
                    angle = (sample / samples) * math.pi * 2
                    x = center_x + radius * math.cos(angle) - float(tile["full_width"]) / 2
                    y = center_y + radius * 0.75 * math.sin(angle) - float(tile["full_height"]) / 2
                    if collides(tile, x, y):
                        continue
                    cost = math.hypot(
                        x + float(tile["full_width"]) / 2 - target_x,
                        y + float(tile["full_height"]) / 2 - target_y,
                    )
                    if cost < best_cost:
                        best = (x, y)
                        best_cost = cost
                radius += step

        if best is not None and not collides(tile, best[0], best[1]):
            tile["x"] = best[0]
            tile["y"] = best[1]
            stamp(tile, best[0], best[1])
            placed.append(tile)

    return placed


def packed_tiles(
    species: list[dict[str, object]],
    bird_assets_dir: Path,
    width: int,
    height: int,
    show_names: bool,
    size_mode: str,
) -> list[dict[str, object]]:
    """Return packed tiles, shrinking gently if the first attempt is too large."""

    masks = load_bird_masks(bird_assets_dir)
    tiles = make_packed_tiles(species, masks, width, height, show_names, size_mode)
    if not tiles:
        return []

    scale = 1.0
    placed: list[dict[str, object]] = []
    for _attempt in range(8):
        attempt_tiles = []
        for tile in tiles:
            attempt_tiles.append(
                {
                    **tile,
                    "image_width": float(tile["image_width"]) * scale,
                    "image_height": float(tile["image_height"]) * scale,
                    "full_width": float(tile["full_width"]) * scale,
                    "full_height": float(tile["image_height"]) * scale
                    + float(tile["label_height"]),
                }
            )
        placed = pack_tiles(attempt_tiles, width, height)
        if len(placed) == len(species):
            return placed
        scale *= 0.91

    return placed if len(placed) == len(species) else []


def paste_rotated(
    canvas: Image.Image,
    source: Image.Image,
    x: int,
    y: int,
    degrees: float,
) -> None:
    """Paste a transparent image with a slight rotation around its center."""

    if abs(degrees) < 0.05:
        canvas.paste(source, (x, y), source)
        return

    rotated = source.rotate(
        degrees,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(0, 0, 0, 0),
    )
    paste_x = x - (rotated.width - source.width) // 2
    paste_y = y - (rotated.height - source.height) // 2
    canvas.paste(rotated, (paste_x, paste_y), rotated)


def draw_bird_label(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    display_name: str,
    show_latin_names: bool,
    initial_size: int,
) -> None:
    """Draw a bird name in the wall-display style."""

    primary_name, latin_name = split_display_name(display_name)
    primary_text = primary_name.upper()
    name_font = fit_text_font(
        draw,
        primary_text,
        max(1, box[2] - box[0]),
        initial_size,
        min_size=9,
    )
    latin_font = fit_text_font(
        draw,
        latin_name,
        max(1, box[2] - box[0]),
        max(8, round(initial_size * 0.58)),
        min_size=7,
    )
    if show_latin_names and latin_name:
        midpoint = box[1] + round((box[3] - box[1]) * 0.52)
        draw_centered_text(draw, (box[0], box[1], box[2], midpoint), primary_text, name_font)
        draw_centered_text(
            draw,
            (box[0], midpoint - 2, box[2], box[3]),
            latin_name,
            latin_font,
            fill=MUTED,
        )
    else:
        draw_centered_text(draw, box, primary_text, name_font)


def draw_packed_collage(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    species: list[dict[str, object]],
    bird_assets_dir: Path,
    box: tuple[int, int, int, int],
    show_names: bool,
    show_latin_names: bool,
    size_mode: str,
) -> bool:
    """Draw the same kind of mask-packed collage as the browser wall."""

    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    tiles = packed_tiles(species, bird_assets_dir, width, height, show_names, size_mode)
    if len(tiles) != len(species):
        return False

    label_font_size = max(13, round(min(width, height) * 0.032))
    for tile in tiles:
        item = tile["species"]
        filename = str(item.get("image_filename") or "")
        bird_image = open_bird_image(
            bird_assets_dir / filename,
            (round(float(tile["image_width"])), round(float(tile["image_height"]))),
        )
        if bird_image is None:
            return False

        x = left + round(float(tile["x"]))
        y = top + round(float(tile["y"]))
        paste_rotated(canvas, bird_image, x, y, float(tile["rotate"]))

        if show_names:
            label_top = y + round(float(tile["image_height"]))
            draw_bird_label(
                draw,
                (
                    x,
                    label_top,
                    x + round(float(tile["full_width"])),
                    label_top + round(float(tile["label_height"])),
                ),
                str(item.get("display_name") or ""),
                show_latin_names,
                label_font_size,
            )

    return True


def draw_grid_collage(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    species: list[dict[str, object]],
    bird_assets_dir: Path,
    box: tuple[int, int, int, int],
    show_names: bool,
    show_latin_names: bool,
) -> None:
    """Fallback layout used when packed masks are unavailable."""

    left, top, right, bottom = box
    max_items = len(species)
    visible_species = species
    columns = 4 if (right - left) >= (bottom - top) else 3
    if max_items <= 4:
        columns = max_items
    rows = (max_items + columns - 1) // columns
    grid_width = right - left
    grid_height = max(1, bottom - top)
    cell_width = grid_width / columns
    cell_height = grid_height / rows
    name_font_size = max(13, round(min(cell_width, cell_height) * 0.11))
    latin_font_size = max(10, round(name_font_size * 0.62))

    for index, item in enumerate(visible_species):
        column = index % columns
        row = index // columns
        cell_left = round(left + column * cell_width)
        cell_top = round(top + row * cell_height)
        cell_right = round(left + (column + 1) * cell_width)
        cell_bottom = round(top + (row + 1) * cell_height)

        display_name = str(item.get("display_name") or "")
        primary_name, latin_name = split_display_name(display_name)
        name_font = fit_text_font(
            draw,
            primary_name.upper(),
            int(cell_width * 0.92),
            name_font_size,
            min_size=10,
        )
        latin_font = fit_text_font(
            draw,
            latin_name,
            int(cell_width * 0.92),
            latin_font_size,
            min_size=8,
        )
        label_height = text_size(draw, primary_name.upper(), name_font)[1] + 12
        if latin_name and show_latin_names:
            label_height += text_size(draw, latin_name, latin_font)[1] + 2

        image_box_height = max(30, (cell_bottom - cell_top) - label_height)
        image_box_width = max(30, cell_right - cell_left)
        filename = str(item.get("image_filename") or "")
        bird_image = open_bird_image(
            bird_assets_dir / filename,
            (round(image_box_width * 0.92), round(image_box_height * 0.92)),
        )
        if bird_image is not None:
            x = cell_left + round(((cell_right - cell_left) - bird_image.width) / 2)
            y = cell_top + round((image_box_height - bird_image.height) / 2)
            canvas.paste(bird_image, (x, y), bird_image)
        else:
            fallback_font = load_font(max(24, round(image_box_height * 0.35)))
            draw_centered_text(
                draw,
                (cell_left, cell_top, cell_right, cell_top + image_box_height),
                shorten(primary_name, width=2, placeholder=""),
                fallback_font,
                fill=MUTED,
            )

        if show_names:
            label_top = cell_top + image_box_height
            draw_centered_text(
                draw,
                (cell_left, label_top, cell_right, label_top + label_height // 2 + 5),
                primary_name.upper(),
                name_font,
            )
            if latin_name and show_latin_names:
                draw_centered_text(
                    draw,
                    (cell_left, label_top + label_height // 2, cell_right, cell_bottom),
                    latin_name,
                    latin_font,
                    fill=MUTED,
                )


def spectra_image(image: Image.Image) -> Image.Image:
    """Reduce an image to the six-color Spectra palette used by ee02."""

    palette = Image.new("P", (1, 1))
    values = [channel for color in SPECTRA_PALETTE for channel in color]
    palette.putpalette(values + [0] * (768 - len(values)))
    return image.quantize(
        palette=palette,
        dither=Image.Dither.FLOYDSTEINBERG,
    ).convert("RGB")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _species_seed(value: str) -> int:
    """Return the same deterministic 32-bit seed as terrace.js."""

    seed = 0
    for character in value:
        seed = (seed * 31 + ord(character)) & 0xFFFFFFFF
    return seed


def _default_terrace_placement(
    species_name: str,
    index: int,
    defaults: dict[str, object],
) -> dict[str, object]:
    seed = _species_seed(species_name)
    slot = TERRACE_FALLBACK_SLOTS[
        (seed + index * 5) % len(TERRACE_FALLBACK_SLOTS)
    ]
    anchor = defaults.get("anchor", {"x": 0.5, "y": 0.82})
    if not isinstance(anchor, dict):
        anchor = {"x": 0.5, "y": 0.82}
    return {
        "x": slot["x"],
        "y": slot["y"],
        "size": _clamp(
            float(slot["size"]) + ((seed % 9) - 4) * 0.004,
            0.09,
            0.18,
        ),
        "anchor": {
            "x": float(anchor.get("x", 0.5)),
            "y": float(anchor.get("y", 0.82)),
        },
        "rotation": ((seed % 11) - 5) * 0.7,
        "zIndex": 20 + seed % 10,
        "placementType": slot["placementType"],
        "imageVariant": (
            "flight" if slot["placementType"] == "airborne" else "still"
        ),
        "alternatives": [],
    }


def _placement_box(
    placement: dict[str, object],
) -> tuple[float, float, float, float]:
    aspect = 0.7 if placement.get("placementType") == "airborne" else 0.86
    size = float(placement.get("size", 0.14))
    anchor = placement.get("anchor", {})
    if not isinstance(anchor, dict):
        anchor = {}
    anchor_x = float(anchor.get("x", 0.5))
    anchor_y = float(anchor.get("y", 0.82))
    x = float(placement.get("x", 0.5))
    y = float(placement.get("y", 0.5))
    bird_height = size * aspect
    return (
        x - size * anchor_x,
        y - bird_height * anchor_y,
        x + size * (1 - anchor_x),
        y + bird_height * (1 - anchor_y) + 0.045,
    )


def _boxes_overlap(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    padding = 0.012
    return not (
        first[2] + padding < second[0]
        or second[2] + padding < first[0]
        or first[3] + padding < second[1]
        or second[3] + padding < first[1]
    )


def _terrace_placements(
    species: list[dict[str, object]],
    config: dict[str, object],
) -> dict[str, dict[str, object]]:
    defaults = config.get("defaults", {})
    configured_species = config.get("species", {})
    if not isinstance(defaults, dict):
        defaults = {}
    if not isinstance(configured_species, dict):
        configured_species = {}

    occupied: list[tuple[float, float, float, float]] = []
    resolved: dict[str, dict[str, object]] = {}
    for index, item in enumerate(species):
        name = str(item.get("species_name", ""))
        base = _default_terrace_placement(name, index, defaults)
        configured = configured_species.get(name)
        if isinstance(configured, dict):
            base.update(configured)
            if not isinstance(base.get("anchor"), dict):
                base["anchor"] = {"x": 0.5, "y": 0.82}

        candidates = [base]
        alternatives = base.get("alternatives", [])
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if isinstance(alternative, dict):
                    candidate = dict(base)
                    candidate.update(alternative)
                    candidate["anchor"] = alternative.get(
                        "anchor",
                        base["anchor"],
                    )
                    candidates.append(candidate)

        chosen = next(
            (
                candidate
                for candidate in candidates
                if not any(
                    _boxes_overlap(_placement_box(candidate), box)
                    for box in occupied
                )
            ),
            None,
        )
        if chosen is None:
            fallback = _default_terrace_placement(name, index + 1, defaults)
            fallback_candidates = []
            for slot in TERRACE_FALLBACK_SLOTS:
                candidate = dict(fallback)
                candidate.update(slot)
                fallback_candidates.append(candidate)
            chosen = next(
                (
                    candidate
                    for candidate in fallback_candidates
                    if not any(
                        _boxes_overlap(_placement_box(candidate), box)
                        for box in occupied
                    )
                ),
                candidates[0],
            )
        occupied.append(_placement_box(chosen))
        resolved[name] = chosen
    return resolved


def _terrace_bird_filename(
    item: dict[str, object],
    placement: dict[str, object],
) -> str:
    variants = item.get("image_variants", [])
    if not isinstance(variants, list):
        variants = []
    filenames = [
        str(variant.get("filename", ""))
        for variant in variants
        if isinstance(variant, dict) and variant.get("filename")
    ]
    if placement.get("imageVariant") == "flight":
        flight = next(
            (
                filename
                for filename in filenames
                if Path(filename).stem.endswith("2")
            ),
            None,
        )
        if flight:
            return flight
    if placement.get("imageVariant") == "still" and filenames:
        return filenames[0]
    return str(item.get("image_filename", ""))


def _paste_anchored_bird(
    scene: Image.Image,
    bird: Image.Image,
    placement: dict[str, object],
) -> None:
    target_width = max(
        8,
        round(float(placement.get("size", 0.14)) * scene.width),
    )
    target_height = max(
        8,
        round(bird.height * target_width / max(1, bird.width)),
    )
    bird = bird.resize((target_width, target_height), Image.Resampling.LANCZOS)
    anchor = placement.get("anchor", {})
    if not isinstance(anchor, dict):
        anchor = {}
    anchor_x = float(anchor.get("x", 0.5)) * target_width
    anchor_y = float(anchor.get("y", 0.82)) * target_height
    diagonal = max(16, math.ceil(math.hypot(target_width, target_height)) * 2)
    layer = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    center = diagonal / 2
    layer.alpha_composite(
        bird,
        (round(center - anchor_x), round(center - anchor_y)),
    )
    rotation = float(placement.get("rotation", 0) or 0)
    if rotation:
        layer = layer.rotate(-rotation, resample=Image.Resampling.BICUBIC)

    destination = (
        round(float(placement.get("x", 0.5)) * scene.width - center),
        round(float(placement.get("y", 0.5)) * scene.height - center),
    )
    shadow_alpha = layer.getchannel("A").filter(ImageFilter.GaussianBlur(4))
    shadow = Image.new("RGBA", layer.size, (43, 33, 23, 0))
    shadow.putalpha(shadow_alpha.point(lambda value: round(value * 0.28)))
    scene.alpha_composite(shadow, (destination[0], destination[1] + 5))
    scene.alpha_composite(layer, destination)


def _draw_terrace_labels(
    scene: Image.Image,
    species: list[dict[str, object]],
    placements: dict[str, dict[str, object]],
    config: dict[str, object],
    show_names: bool,
    show_latin_names: bool,
) -> None:
    if not show_names:
        return
    draw = ImageDraw.Draw(scene)
    font = load_font(17)
    latin_font = load_font(11, italic=True)
    labels = config.get("labels", {})
    if not isinstance(labels, dict):
        labels = {}
    for item in species:
        name = str(item.get("species_name", ""))
        placement = placements[name]
        label = placement.get("label") or labels.get(name, {})
        if not isinstance(label, dict):
            label = {}
        label_x = _clamp(
            float(label.get("x", placement.get("x", 0.5))),
            0.09,
            0.91,
        )
        default_offset = (
            0.055 if placement.get("placementType") == "ground" else 0.075
        )
        label_y = _clamp(
            float(
                label.get(
                    "y",
                    float(placement.get("y", 0.5))
                    + float(label.get("yOffset", default_offset)),
                )
            ),
            0.04,
            0.96,
        )
        display_name, latin_name = split_display_name(
            str(item.get("display_name", name))
        )
        text_box = draw.textbbox((0, 0), display_name, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        latin_width = 0
        latin_height = 0
        if show_latin_names and latin_name:
            latin_box = draw.textbbox((0, 0), latin_name, font=latin_font)
            latin_width = latin_box[2] - latin_box[0]
            latin_height = latin_box[3] - latin_box[1]
        content_width = max(text_width, latin_width)
        content_height = text_height + (latin_height + 3 if latin_height else 0)
        center_x = round(label_x * scene.width)
        center_y = round(label_y * scene.height)
        padding_x, padding_y = 11, 6
        pill = (
            center_x - content_width // 2 - padding_x,
            center_y - content_height // 2 - padding_y,
            center_x + content_width // 2 + padding_x,
            center_y + content_height // 2 + padding_y,
        )
        draw.rounded_rectangle(
            pill,
            radius=16,
            fill=(248, 239, 216, 218),
            outline=(43, 33, 23, 42),
            width=1,
        )
        primary_y = center_y - (latin_height + 3) // 2 if latin_height else center_y
        draw.text(
            (center_x, primary_y),
            display_name,
            font=font,
            fill=TEXT,
            anchor="mm",
        )
        if latin_height:
            draw.text(
                (center_x, primary_y + text_height // 2 + latin_height // 2 + 3),
                latin_name,
                font=latin_font,
                fill=MUTED,
                anchor="mm",
            )


def render_eink_terrace_image(
    wall_data: dict[str, object],
    bird_assets_dir: Path,
    terrace_assets_dir: Path,
    width: int | None = None,
    height: int | None = None,
    *,
    landscape: bool = False,
    output_format: str = "JPEG",
    use_spectra_palette: bool = False,
) -> bytes:
    """Render the configured terrace scene for the eInk endpoint."""

    if width is None or height is None:
        width, height = LANDSCAPE_SIZE if landscape else PORTRAIT_SIZE
    width = max(320, min(int(width), 2400))
    height = max(240, min(int(height), 2400))
    background_path = terrace_assets_dir / "terrasse-baggrund-eink.png"
    uses_eink_background = background_path.is_file()
    if not uses_eink_background:
        background_path = terrace_assets_dir / "terrasse-baggrund.jpg"
    foreground_path = (
        None
        if uses_eink_background
        else terrace_assets_dir / "terrasse-forgrund.png"
    )
    with Image.open(background_path) as source:
        scene = source.convert("RGBA")

    species = list(wall_data.get("species") or [])
    placement_config = wall_data.get("terrace_placements", {})
    if not isinstance(placement_config, dict):
        placement_config = {}
    placements = _terrace_placements(species, placement_config)
    if uses_eink_background:
        robin_name = "Erithacus rubecula_European Robin"
        robin_placement = placements.get(robin_name)
        if robin_placement is not None:
            robin_placement = dict(robin_placement)
            robin_placement["y"] = 0.515
            placements[robin_name] = robin_placement
    for item in sorted(
        species,
        key=lambda value: int(
            placements[str(value.get("species_name", ""))].get("zIndex", 20)
        ),
    ):
        name = str(item.get("species_name", ""))
        filename = _terrace_bird_filename(item, placements[name])
        image_path = bird_assets_dir / filename
        if image_path.is_file():
            with Image.open(image_path) as source:
                _paste_anchored_bird(
                    scene,
                    source.convert("RGBA"),
                    placements[name],
                )

    if foreground_path is not None and foreground_path.is_file():
        with Image.open(foreground_path) as source:
            foreground = source.convert("RGBA").resize(
                scene.size,
                Image.Resampling.LANCZOS,
            )
        scene.alpha_composite(foreground)
    _draw_terrace_labels(
        scene,
        species,
        placements,
        placement_config,
        bool(wall_data.get("show_names", True)),
        bool(wall_data.get("show_latin_names", True)),
    )

    show_footer = bool(wall_data.get("show_footer", True))
    minimum_header_height = max(
        76,
        round(height * (0.13 if show_footer else 0.10)),
    )
    full_width_scene_height = round(width * scene.height / scene.width)
    header_height = max(
        minimum_header_height,
        height - full_width_scene_height,
    )
    available_scene_height = max(1, height - header_height)
    scene_scale = min(
        width / scene.width,
        available_scene_height / scene.height,
    )
    scene_size = (
        max(1, round(scene.width * scene_scale)),
        max(1, round(scene.height * scene_scale)),
    )
    scaled_scene = scene.resize(scene_size, Image.Resampling.LANCZOS)
    page = Image.new(
        "RGBA",
        (width, height),
        TERRACE_EINK_BACKGROUND + (255,),
    )
    scene_x = round((width - scaled_scene.width) / 2)
    page.alpha_composite(scaled_scene, (scene_x, header_height))

    draw = ImageDraw.Draw(page)
    margin = max(10, round(min(width, height) * 0.025))
    title = str(wall_data.get("site_title") or "Fuglene i haven")
    title_bottom = round(header_height * (0.66 if show_footer else 0.94))
    title_font = fit_text_font(
        draw,
        title,
        width - margin * 2,
        max(26, round(title_bottom * 0.52)),
        min_size=18,
    )
    draw_centered_text(
        draw,
        (margin * 2, 0, width - margin * 2, title_bottom),
        title,
        title_font,
    )
    if show_footer and species:
        latest = latest_species(species)
        if latest is not None:
            primary_name, _ = split_display_name(
                str(latest.get("display_name") or "")
            )
            footer_text = (
                f"Sidst hørt: {primary_name}, "
                f"{format_seen_time(str(latest.get('latest_analyzed_at') or ''))} "
                f"· opdateret {datetime.now().strftime('%H.%M')}"
            )
            footer_font = fit_text_font(
                draw,
                footer_text,
                width - margin * 2,
                max(15, round(header_height * 0.16)),
                min_size=11,
            )
            draw_centered_text(
                draw,
                (margin * 2, title_bottom - 2, width - margin * 2, header_height),
                footer_text,
                footer_font,
                fill=MUTED,
            )

    page = page.convert("RGB")

    result = spectra_image(page) if use_spectra_palette else page
    buffer = BytesIO()
    if output_format.upper() == "PNG":
        result.save(buffer, format="PNG", optimize=True)
    else:
        result.save(buffer, format="JPEG", quality=98, subsampling=0)
    return buffer.getvalue()


def render_eink_wall_image(
    wall_data: dict[str, object],
    bird_assets_dir: Path,
    width: int | None = None,
    height: int | None = None,
    *,
    landscape: bool = False,
    output_format: str = "JPEG",
    use_spectra_palette: bool = False,
    collage_insets: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    """Render wall data as an image suitable for an eInk controller."""

    if width is None or height is None:
        width, height = LANDSCAPE_SIZE if landscape else PORTRAIT_SIZE
    width = max(320, min(int(width), 2400))
    height = max(240, min(int(height), 2400))
    background = parse_hex_color(wall_data.get("eink_background"), BACKGROUND)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    margin = max(16, round(min(width, height) * 0.045))
    title = str(wall_data.get("site_title") or "Fuglene i haven")
    title_font = fit_text_font(
        draw,
        title,
        width - margin * 2,
        max(30, round(height * 0.09)),
        min_size=22,
    )
    title_height = text_size(draw, title, title_font)[1]
    draw_centered_text(
        draw,
        (margin, margin // 2, width - margin, margin // 2 + title_height + 12),
        title,
        title_font,
    )

    species = list(wall_data.get("species") or [])
    show_footer = bool(wall_data.get("show_footer", True))
    footer_height = max(34, round(height * 0.035)) if show_footer else 0
    inset_left, inset_right, inset_bottom = collage_insets
    collage_left = margin + max(0, inset_left)
    collage_right = width - margin - max(0, inset_right)
    collage_top = margin + title_height + 20
    collage_bottom = height - margin - footer_height - max(0, inset_bottom)

    if not species:
        empty_font = load_font(max(22, round(height * 0.055)))
        draw_centered_text(
            draw,
            (collage_left, collage_top, collage_right, collage_bottom),
            "Stationen venter pÃ¥ nye fuglestemmer",
            empty_font,
            fill=MUTED,
        )
    else:
        visible_species = species
        collage_box = (
            collage_left,
            collage_top,
            collage_right,
            collage_bottom,
        )
        show_names = bool(wall_data.get("show_names", True))
        show_latin_names = bool(wall_data.get("show_latin_names", True))
        size_mode = str(wall_data.get("size_mode") or "common")
        if not draw_packed_collage(
            image,
            draw,
            visible_species,
            bird_assets_dir,
            collage_box,
            show_names,
            show_latin_names,
            size_mode,
        ):
            draw_grid_collage(
                image,
                draw,
                visible_species,
                bird_assets_dir,
                collage_box,
                show_names,
                show_latin_names,
            )

    if show_footer and species:
        latest = latest_species(species)
        if latest is not None:
            primary_name, _ = split_display_name(
                str(latest.get("display_name") or "")
            )
            footer_text = (
                f"Sidst hørt: {primary_name}, "
                f"{format_seen_time(str(latest.get('latest_analyzed_at') or ''))}."
            )
            footer_font = fit_text_font(
                draw,
                footer_text,
                width - margin * 2,
                max(15, round(footer_height * 0.45)),
                min_size=11,
            )
            draw_centered_text(
                draw,
                (margin, height - footer_height, width - margin, height),
                footer_text,
                footer_font,
                fill=MUTED,
            )

    result = spectra_image(image) if use_spectra_palette else image
    image_format = output_format.upper()
    buffer = BytesIO()
    if image_format == "PNG":
        result.save(buffer, format="PNG", optimize=True)
    else:
        result.convert("RGB").save(
            buffer,
            format="JPEG",
            quality=98,
            subsampling=0,
        )
    return buffer.getvalue()


def render_eink_decorative_image(
    wall_data: dict[str, object],
    bird_assets_dir: Path,
    decorative_assets_dir: Path,
    width: int | None = None,
    height: int | None = None,
    *,
    landscape: bool = False,
    output_format: str = "JPEG",
    use_spectra_palette: bool = False,
) -> bytes:
    """Render the simple wall with the botanical frame as a foreground."""

    if width is None or height is None:
        width, height = LANDSCAPE_SIZE if landscape else PORTRAIT_SIZE
    width = max(320, min(int(width), 2400))
    height = max(240, min(int(height), 2400))

    simple_png = render_eink_wall_image(
        wall_data,
        bird_assets_dir,
        width,
        height,
        landscape=landscape,
        output_format="PNG",
        use_spectra_palette=False,
        collage_insets=(
            round(width * 0.15),
            round(width * 0.15),
            round(height * 0.14),
        ),
    )
    with Image.open(BytesIO(simple_png)) as source:
        page = source.convert("RGBA")

    frame_path = decorative_assets_dir / "botanisk-ramme.png"
    if frame_path.exists():
        with Image.open(frame_path) as source:
            frame_source = source.convert("RGBA")

        source_width, source_height = frame_source.size
        frame_height = min(height, round(width * source_height / source_width))
        frame_width = round(frame_height * source_width / source_height)
        frame = frame_source.resize(
            (frame_width, frame_height),
            Image.Resampling.LANCZOS,
        )

        # The source is almost fully transparent at x=716. Splitting there keeps
        # the left-hand foliage intact when the two sides move apart on a wide
        # display, matching the responsive browser presentation.
        split_x = round(frame_width * 716 / source_width)
        y = height - frame_height
        left = frame.crop((0, 0, split_x, frame_height))
        right = frame.crop((split_x, 0, frame_width, frame_height))
        page.alpha_composite(left, (0, y))
        page.alpha_composite(right, (width - right.width, y))

    image = page.convert("RGB")
    result = spectra_image(image) if use_spectra_palette else image
    buffer = BytesIO()
    if output_format.upper() == "PNG":
        result.save(buffer, format="PNG", optimize=True)
    else:
        result.save(buffer, format="JPEG", quality=98, subsampling=0)
    return buffer.getvalue()
