const collageEl = document.querySelector("#bird-collage");
const wallEl = document.querySelector(".wall");
const wallTitleEl = document.querySelector("#wall-title");
const wallUpdatedEl = document.querySelector("#wall-updated");
const wallLatestEl = document.querySelector("#wall-latest");

const WALL_REFRESH_MS = 30000;
const DAILY_RELOAD_HOUR = 3;
const FREQUENCY_CHANGE_RATIO = 0.18;
const FREQUENCY_CHANGE_ABSOLUTE = 2;
const MASK_URL = "/assets/birds/masks.json?v=20260715-blackbird2-cutout";
const GRID_STRIDE = 4;
const COLLAGE_PAD_CELLS = 2;

let birdMasks = {};
let masksReady = false;
let lastWallData = null;
let lastRenderedWallData = null;
let nextDailyReloadAt = nextDailyReloadTime();

const masksPromise = fetch(MASK_URL)
  .then((response) => {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  })
  .then((data) => {
    birdMasks = data;
    masksReady = true;
  })
  .catch((error) => {
    console.warn("Kunne ikke hente fuglemasker", error);
  });

function formatSeenTime(value) {
  if (!value) {
    return "ukendt tidspunkt";
  }

  const date = new Date(value);
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 90) {
    return "lige nu";
  }

  const minutes = Math.round(seconds / 60);
  if (minutes < 90) {
    return `${minutes} min. siden`;
  }

  const hours = Math.round(minutes / 60);
  if (hours < 36) {
    return `${hours} t. siden`;
  }

  return date.toLocaleDateString("da-DK");
}

function speciesSeed(text) {
  let seed = 0;
  for (const char of text) {
    seed = (seed * 31 + char.charCodeAt(0)) % 9973;
  }
  return seed;
}

function initials(displayName) {
  return displayName
    .split("/")
    .map((part) => part.trim()[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function splitDisplayName(displayName) {
  const parts = displayName.split("/").map((part) => part.trim());
  if (parts.length < 2) {
    return { primaryName: displayName, secondaryName: "" };
  }

  return {
    primaryName: parts[0],
    secondaryName: parts.slice(1).join(" / "),
  };
}

function primaryDisplayName(displayName) {
  return splitDisplayName(displayName).primaryName;
}

function primaryNameScale(name) {
  if (name.length <= 8) {
    return 1;
  }
  return Math.max(0.68, 8 / name.length);
}

function renderEmptyState() {
  collageEl.replaceChildren();
  collageEl.removeAttribute("data-layout");
  const empty = document.createElement("section");
  empty.className = "empty-wall";
  empty.innerHTML = `
    <p class="eyebrow">Ingen fugle i visningen</p>
    <h2>Stationen venter på nye fuglestemmer</h2>
    <p>Start stationen fra settings, eller juster filteret, så fyldes væggen her.</p>
  `;
  collageEl.append(empty);
}

function nextDailyReloadTime(now = new Date()) {
  const reloadAt = new Date(now);
  reloadAt.setHours(DAILY_RELOAD_HOUR, 0, 0, 0);
  if (reloadAt <= now) {
    reloadAt.setDate(reloadAt.getDate() + 1);
  }
  return reloadAt.getTime();
}

function maybeReloadDaily() {
  if (Date.now() >= nextDailyReloadAt) {
    nextDailyReloadAt = nextDailyReloadTime();
    window.location.reload();
  }
}

function speciesCountMap(speciesList) {
  return new Map(
    speciesList.map((species) => [species.species_name, Number(species.count) || 0]),
  );
}

function hasSpeciesSetChanged(previousSpecies, nextSpecies) {
  if (previousSpecies.length !== nextSpecies.length) {
    return true;
  }

  const previousNames = new Set(
    previousSpecies.map((species) => species.species_name),
  );
  return nextSpecies.some((species) => !previousNames.has(species.species_name));
}

function hasFrequencyShift(previousSpecies, nextSpecies) {
  const previousCounts = speciesCountMap(previousSpecies);
  const nextCounts = speciesCountMap(nextSpecies);
  const maxNextCount = Math.max(...nextCounts.values(), 1);

  for (const [speciesName, nextCount] of nextCounts) {
    const previousCount = previousCounts.get(speciesName) || 0;
    const absoluteChange = Math.abs(nextCount - previousCount);
    const relativeChange = absoluteChange / maxNextCount;
    if (
      absoluteChange >= FREQUENCY_CHANGE_ABSOLUTE &&
      relativeChange >= FREQUENCY_CHANGE_RATIO
    ) {
      return true;
    }
  }

  return false;
}

function displaySettingsChanged(previousData, nextData) {
  return (
    previousData.show_names !== nextData.show_names ||
    previousData.show_latin_names !== nextData.show_latin_names ||
    previousData.size_mode !== nextData.size_mode ||
    previousData.limit !== nextData.limit ||
    previousData.recent_minutes !== nextData.recent_minutes ||
    previousData.show_shadows !== nextData.show_shadows
  );
}

function shouldRedraw(previousData, nextData) {
  if (!previousData) {
    return true;
  }
  if (displaySettingsChanged(previousData, nextData)) {
    return true;
  }
  if (hasSpeciesSetChanged(previousData.species, nextData.species)) {
    return true;
  }
  return hasFrequencyShift(previousData.species, nextData.species);
}

function imageStem(species) {
  if (!species.image_filename) {
    return "";
  }
  return species.image_filename.replace(/\.[^.]+$/, "");
}

function decodeMask(maskRecord) {
  if (!maskRecord || maskRecord.cells) {
    return maskRecord;
  }

  const bytes = atob(maskRecord.bits);
  const cells = [];
  for (let y = 0; y < maskRecord.h; y += 1) {
    for (let x = 0; x < maskRecord.w; x += 1) {
      const index = y * maskRecord.w + x;
      const byte = bytes.charCodeAt(index >> 3);
      if ((byte >> (7 - (index & 7))) & 1) {
        cells.push([x, y]);
      }
    }
  }

  maskRecord.cells = cells;
  return maskRecord;
}

function speciesSizeScore(species, sizeMode, maxCount) {
  if (sizeMode === "equal") {
    return 1;
  }
  if (sizeMode === "rare") {
    return Math.pow(Math.max(1, maxCount - species.count + 1), 0.62);
  }
  return Math.pow(Math.max(1, species.count), 0.62);
}

function makeTiles(speciesList, maxCount, width, height, showNames, sizeMode) {
  const viewportArea = width * height;
  const budget = viewportArea * (speciesList.length <= 4 ? 0.5 : 0.42);
  const minArea = viewportArea * 0.012;
  const scored = speciesList
    .map((species, index) => {
      const mask = decodeMask(birdMasks[imageStem(species)]);
      if (!mask || !species.image_url) {
        return null;
      }

      const countWeight = maxCount > 0 ? species.count / maxCount : 0;
      const score = speciesSizeScore(species, sizeMode, maxCount);
      const seed = speciesSeed(species.species_name);
      return {
        species,
        index,
        mask,
        showNames,
        score,
        aspectRatio: mask.w / mask.h,
        rotate: ((seed % 9) - 4) * 0.7,
        countWeight,
      };
    })
    .filter(Boolean);

  const scoreSum = scored.reduce((sum, tile) => sum + tile.score, 0) || 1;
  return scored.map((tile) => {
    const area = Math.max(minArea, (budget * tile.score) / scoreSum);
    const imageWidth = Math.sqrt(area * tile.aspectRatio);
    const imageHeight = imageWidth / tile.aspectRatio;
    return {
      ...tile,
      imageWidth,
      imageHeight,
      fullWidth: imageWidth,
      fullHeight: imageHeight + (showNames ? 54 : 0),
    };
  });
}

function packTiles(tiles, width, height) {
  const gridWidth = Math.ceil(width / GRID_STRIDE) + 2;
  const gridHeight = Math.ceil(height / GRID_STRIDE) + 2;
  const grid = new Uint8Array(gridWidth * gridHeight);
  const centerX = width / 2;
  const centerY = height / 2;
  const placed = [];

  function cellRange(tile, x, y, cell) {
    const scaleX = tile.imageWidth / tile.mask.w;
    const scaleY = tile.imageHeight / tile.mask.h;
    return {
      x0: Math.max(0, Math.floor((x + cell[0] * scaleX) / GRID_STRIDE)),
      y0: Math.max(0, Math.floor((y + cell[1] * scaleY) / GRID_STRIDE)),
      x1: Math.min(
        gridWidth - 1,
        Math.floor((x + (cell[0] + 1) * scaleX) / GRID_STRIDE),
      ),
      y1: Math.min(
        gridHeight - 1,
        Math.floor((y + (cell[1] + 1) * scaleY) / GRID_STRIDE),
      ),
    };
  }

  function collides(tile, x, y) {
    if (
      x < 0 ||
      y < 0 ||
      x + tile.fullWidth > width ||
      y + tile.fullHeight > height
    ) {
      return true;
    }

    const maskCollides = tile.mask.cells.some((cell) => {
      const range = cellRange(tile, x, y, cell);
      for (let gy = range.y0; gy <= range.y1; gy += 1) {
        const offset = gy * gridWidth;
        for (let gx = range.x0; gx <= range.x1; gx += 1) {
          if (grid[offset + gx]) {
            return true;
          }
        }
      }
      return false;
    });
    if (maskCollides) {
      return true;
    }

    if (tile.showNames) {
      const labelY0 = Math.floor((y + tile.imageHeight) / GRID_STRIDE);
      const labelY1 = Math.min(
        gridHeight - 1,
        Math.floor((y + tile.fullHeight) / GRID_STRIDE),
      );
      const labelX0 = Math.max(
        0,
        Math.floor((x + tile.fullWidth * 0.12) / GRID_STRIDE),
      );
      const labelX1 = Math.min(
        gridWidth - 1,
        Math.floor((x + tile.fullWidth * 0.88) / GRID_STRIDE),
      );
      for (let gy = labelY0; gy <= labelY1; gy += 1) {
        const offset = gy * gridWidth;
        for (let gx = labelX0; gx <= labelX1; gx += 1) {
          if (grid[offset + gx]) {
            return true;
          }
        }
      }
    }

    return false;
  }

  function stamp(tile, x, y) {
    tile.mask.cells.forEach((cell) => {
      const range = cellRange(tile, x, y, cell);
      const y0 = Math.max(0, range.y0 - COLLAGE_PAD_CELLS);
      const y1 = Math.min(gridHeight - 1, range.y1 + COLLAGE_PAD_CELLS);
      const x0 = Math.max(0, range.x0 - COLLAGE_PAD_CELLS);
      const x1 = Math.min(gridWidth - 1, range.x1 + COLLAGE_PAD_CELLS);
      for (let gy = y0; gy <= y1; gy += 1) {
        const offset = gy * gridWidth;
        for (let gx = x0; gx <= x1; gx += 1) {
          grid[offset + gx] = 1;
        }
      }
    });

    if (tile.showNames) {
      const labelY0 = Math.max(
        0,
        Math.floor((y + tile.imageHeight) / GRID_STRIDE) - COLLAGE_PAD_CELLS,
      );
      const labelY1 = Math.min(
        gridHeight - 1,
        Math.floor((y + tile.fullHeight) / GRID_STRIDE) + COLLAGE_PAD_CELLS,
      );
      const labelX0 = Math.max(
        0,
        Math.floor((x + tile.fullWidth * 0.12) / GRID_STRIDE) - COLLAGE_PAD_CELLS,
      );
      const labelX1 = Math.min(
        gridWidth - 1,
        Math.floor((x + tile.fullWidth * 0.88) / GRID_STRIDE) + COLLAGE_PAD_CELLS,
      );
      for (let gy = labelY0; gy <= labelY1; gy += 1) {
        const offset = gy * gridWidth;
        for (let gx = labelX0; gx <= labelX1; gx += 1) {
          grid[offset + gx] = 1;
        }
      }
    }
  }

  tiles
    .slice()
    .sort((a, b) => b.imageWidth * b.imageHeight - a.imageWidth * a.imageHeight)
    .forEach((tile, index) => {
      let best = null;
      let bestCost = Number.POSITIVE_INFINITY;
      const step = Math.max(GRID_STRIDE, Math.min(tile.imageWidth, tile.imageHeight) * 0.08);
      const maxRadius = Math.max(width, height);

      if (index === 0) {
        best = {
          x: centerX - tile.fullWidth / 2,
          y: centerY - tile.fullHeight / 2,
        };
      } else {
        const centerOfMass = placed.reduce(
          (acc, placedTile) => {
            const area = placedTile.imageWidth * placedTile.imageHeight;
            acc.x += (placedTile.x + placedTile.fullWidth / 2) * area;
            acc.y += (placedTile.y + placedTile.fullHeight / 2) * area;
            acc.area += area;
            return acc;
          },
          { x: 0, y: 0, area: 0 },
        );
        const targetX = centerOfMass.x / centerOfMass.area;
        const targetY = centerOfMass.y / centerOfMass.area;

        for (let radius = 0; radius <= maxRadius && !best; radius += step) {
          const samples = Math.max(24, Math.floor(radius / 2));
          for (let sample = 0; sample < samples; sample += 1) {
            const angle = (sample / samples) * Math.PI * 2;
            const x = centerX + radius * Math.cos(angle) - tile.fullWidth / 2;
            const y = centerY + radius * 0.75 * Math.sin(angle) - tile.fullHeight / 2;
            if (collides(tile, x, y)) {
              continue;
            }

            const cost = Math.hypot(
              x + tile.fullWidth / 2 - targetX,
              y + tile.fullHeight / 2 - targetY,
            );
            if (cost < bestCost) {
              best = { x, y };
              bestCost = cost;
            }
          }
        }
      }

      if (best && !collides(tile, best.x, best.y)) {
        tile.x = best.x;
        tile.y = best.y;
        stamp(tile, tile.x, tile.y);
        placed.push(tile);
      }
    });

  return placed;
}

function renderPackedCollage(
  speciesList,
  maxCount,
  showNames,
  showLatinNames,
  sizeMode,
) {
  const width = collageEl.clientWidth;
  const height = collageEl.clientHeight;
  if (!masksReady || width < 1 || height < 1) {
    return false;
  }

  const tiles = makeTiles(
    speciesList,
    maxCount,
    width,
    height,
    showNames,
    sizeMode,
  );
  if (tiles.length !== speciesList.length) {
    return false;
  }

  let placed = [];
  let scale = 1;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const attemptTiles = tiles.map((tile) => ({
      ...tile,
      imageWidth: tile.imageWidth * scale,
      imageHeight: tile.imageHeight * scale,
      fullWidth: tile.fullWidth * scale,
      fullHeight: tile.imageHeight * scale + (tile.showNames ? 54 : 0),
    }));
    placed = packTiles(attemptTiles, width, height);
    if (placed.length === speciesList.length) {
      break;
    }
    scale *= 0.91;
  }

  if (placed.length !== speciesList.length) {
    return false;
  }

  collageEl.dataset.layout = "packed";
  collageEl.replaceChildren();
  placed.forEach((tile) => {
    const card = renderBirdCard(
      tile.species,
      tile.index,
      maxCount,
      showNames,
      showLatinNames,
    );
    card.style.left = `${tile.x}px`;
    card.style.top = `${tile.y}px`;
    card.style.width = `${tile.fullWidth}px`;
    card.style.setProperty("--image-width", `${tile.imageWidth}px`);
    card.style.setProperty("--image-height", `${tile.imageHeight}px`);
    card.style.setProperty("--rotation", `${tile.rotate}deg`);
    collageEl.append(card);
  });
  return true;
}

function renderBirdCard(
  species,
  index,
  maxCount,
  showNames = true,
  showLatinNames = true,
) {
  const seed = speciesSeed(species.species_name);
  const card = document.createElement("article");
  const countWeight = maxCount > 0 ? species.count / maxCount : 0;
  const size = Math.round(124 + countWeight * 88);
  const rotate = ((seed % 9) - 4) * 0.7;
  const hue = (seed * 17) % 360;

  card.className = "bird-card";
  card.style.setProperty("--card-size", `${size}px`);
  card.style.setProperty("--rotation", `${rotate}deg`);
  card.style.setProperty("--hue", String(hue));
  card.style.setProperty("--delay", `${index * 70}ms`);

  const mark = document.createElement("div");
  mark.className = "bird-mark";
  if (species.image_url) {
    mark.dataset.hasImage = "true";
    const image = document.createElement("img");
    image.src = species.image_url;
    image.alt = species.display_name;
    mark.append(image);
  } else {
    mark.textContent = initials(species.display_name);
  }

  if (!showNames) {
    card.append(mark);
    return card;
  }

  const { primaryName, secondaryName } = splitDisplayName(species.display_name);
  const title = document.createElement("h2");
  const primaryTitle = document.createElement("span");
  primaryTitle.className = "bird-name-primary";
  primaryTitle.textContent = primaryName;
  primaryTitle.style.setProperty("--name-scale", primaryNameScale(primaryName));
  title.append(primaryTitle);

  if (showLatinNames && secondaryName) {
    const secondaryTitle = document.createElement("span");
    secondaryTitle.className = "bird-name-secondary";
    secondaryTitle.textContent = secondaryName;
    title.append(secondaryTitle);
  }

  card.append(mark, title);
  return card;
}

function renderWall(data) {
  lastWallData = data;
  lastRenderedWallData = data;
  wallTitleEl.textContent = data.site_title || "Fuglene i haven";
  document.title = data.site_title || "Fuglene i haven";
  wallEl.dataset.showFooter = data.show_footer !== false ? "true" : "false";
  wallEl.dataset.showShadows = data.show_shadows === true ? "true" : "false";
  wallUpdatedEl.textContent = `Opdateret ${new Date(data.updated_at).toLocaleTimeString(
    "da-DK",
    { hour: "2-digit", minute: "2-digit" },
  )}`;

  if (data.species.length === 0) {
    wallLatestEl.textContent = "Ingen detektioner over filteret endnu";
    renderEmptyState();
    return;
  }

  const maxCount = Math.max(...data.species.map((species) => species.count));
  const showNames = data.show_names !== false;
  const showLatinNames = data.show_latin_names !== false;
  const sizeMode = data.size_mode || "common";
  const latest = data.species.reduce((current, species) => {
    const speciesTime = new Date(species.latest_analyzed_at).getTime();
    const currentTime = new Date(current.latest_analyzed_at).getTime();

    if (speciesTime > currentTime) {
      return species;
    }
    if (speciesTime === currentTime && species.count < current.count) {
      return species;
    }
    return current;
  });

  wallLatestEl.textContent = `Sidst hørt: ${primaryDisplayName(
    latest.display_name,
  )}, ${formatSeenTime(
    latest.latest_analyzed_at,
  )}.`;
  if (
    renderPackedCollage(
      data.species,
      maxCount,
      showNames,
      showLatinNames,
      sizeMode,
    )
  ) {
    return;
  }

  collageEl.dataset.layout = "fallback";
  collageEl.replaceChildren();
  data.species.forEach((species, index) => {
    collageEl.append(
      renderBirdCard(species, index, maxCount, showNames, showLatinNames),
    );
  });
}

function updateWallFooter(data) {
  wallTitleEl.textContent = data.site_title || "Fuglene i haven";
  document.title = data.site_title || "Fuglene i haven";
  wallUpdatedEl.textContent = `Opdateret ${new Date(data.updated_at).toLocaleTimeString(
    "da-DK",
    { hour: "2-digit", minute: "2-digit" },
  )}`;

  if (data.species.length === 0) {
    wallLatestEl.textContent = "Ingen detektioner over filteret endnu";
    return;
  }

  const latest = data.species.reduce((current, species) => {
    const speciesTime = new Date(species.latest_analyzed_at).getTime();
    const currentTime = new Date(current.latest_analyzed_at).getTime();

    if (speciesTime > currentTime) {
      return species;
    }
    if (speciesTime === currentTime && species.count < current.count) {
      return species;
    }
    return current;
  });

  wallLatestEl.textContent = `Sidst hørt: ${primaryDisplayName(
    latest.display_name,
  )}, ${formatSeenTime(
    latest.latest_analyzed_at,
  )}.`;
}

async function loadWall() {
  try {
    const response = await fetch("/api/wall?min_confidence=0.05");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    if (shouldRedraw(lastRenderedWallData, data)) {
      renderWall(data);
    } else {
      lastWallData = data;
      updateWallFooter(data);
    }
    maybeReloadDaily();
  } catch (error) {
    wallUpdatedEl.textContent = "Fejl";
    wallLatestEl.textContent = `Kunne ikke hente data: ${error.message}`;
  }
}

loadWall();
window.setInterval(loadWall, WALL_REFRESH_MS);
window.setInterval(maybeReloadDaily, 60 * 60 * 1000);

masksPromise.then(() => {
  if (lastWallData) {
    renderWall(lastWallData);
  }
});

window.addEventListener("resize", () => {
  if (lastWallData) {
    window.requestAnimationFrame(() => renderWall(lastWallData));
  }
});
