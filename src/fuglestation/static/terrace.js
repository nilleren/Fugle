const REFRESH_MS = 30000;
const CONFIG_URL = "/static/terrace-placements.json?v=20261018-2";
const DEBUG_MODE = new URLSearchParams(window.location.search).get("debug") === "1";

const sceneEl = document.querySelector("#terrace-scene");
const birdLayerEl = document.querySelector("#bird-layer");
const labelLayerEl = document.querySelector("#label-layer");
const statusEl = document.querySelector("#terrace-status");
const debugPanelEl = document.querySelector("#debug-panel");
const speciesSelectEl = document.querySelector("#debug-species");
const sizeInputEl = document.querySelector("#debug-size");
const sizeValueEl = document.querySelector("#debug-size-value");
const rotationInputEl = document.querySelector("#debug-rotation");
const rotationValueEl = document.querySelector("#debug-rotation-value");
const coordinatesEl = document.querySelector("#debug-coordinates");
const copyConfigEl = document.querySelector("#copy-config");

const FALLBACK_SLOTS = [
  { x: 0.18, y: 0.19, size: 0.15, placementType: "airborne" },
  { x: 0.4, y: 0.13, size: 0.14, placementType: "airborne" },
  { x: 0.65, y: 0.2, size: 0.14, placementType: "airborne" },
  { x: 0.82, y: 0.34, size: 0.13, placementType: "airborne" },
  { x: 0.18, y: 0.42, size: 0.14, placementType: "perched" },
  { x: 0.45, y: 0.37, size: 0.13, placementType: "airborne" },
  { x: 0.69, y: 0.43, size: 0.13, placementType: "perched" },
  { x: 0.84, y: 0.57, size: 0.12, placementType: "perched" },
  { x: 0.12, y: 0.66, size: 0.15, placementType: "ground" },
  { x: 0.3, y: 0.76, size: 0.14, placementType: "ground" },
  { x: 0.55, y: 0.72, size: 0.13, placementType: "perched" },
  { x: 0.76, y: 0.8, size: 0.13, placementType: "ground" },
];

let placementConfig = { defaults: {}, species: {} };
let workingPlacements = {};
let wallData = null;
let selectedSpeciesName = "";
let dragState = null;

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function round(value, digits = 4) {
  return Number(Number(value).toFixed(digits));
}

function speciesSeed(text) {
  let seed = 0;
  for (const char of text) {
    seed = (seed * 31 + char.charCodeAt(0)) >>> 0;
  }
  return seed;
}

function splitDisplayName(displayName) {
  return displayName.split("/")[0].trim();
}

function splitBirdNames(displayName) {
  const parts = String(displayName || "")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean);
  return {
    primary: parts[0] || "Ukendt fugl",
    latin: parts.slice(1).join(" / "),
  };
}

function initials(displayName) {
  return splitDisplayName(displayName)
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function imageVariant(species, placement) {
  const variants = Array.isArray(species.image_variants)
    ? species.image_variants
    : [];
  if (placement.imageVariant === "flight") {
    const flight = variants.find((variant) => /2\.[^.]+$/i.test(variant.filename));
    return flight?.url || species.image_url;
  }
  if (placement.imageVariant === "still") {
    return species.still_image_url || variants[0]?.url || species.image_url;
  }
  return species.image_url;
}

function defaultPlacement(speciesName, index) {
  const seed = speciesSeed(speciesName);
  const slot = FALLBACK_SLOTS[(seed + index * 5) % FALLBACK_SLOTS.length];
  const defaultAnchor = placementConfig.defaults.anchor || { x: 0.5, y: 0.82 };
  const sizeJitter = ((seed % 9) - 4) * 0.004;
  return {
    x: slot.x,
    y: slot.y,
    size: clamp(slot.size + sizeJitter, 0.09, 0.18),
    anchor: { ...defaultAnchor },
    rotation: ((seed % 11) - 5) * 0.7,
    zIndex: 20 + (seed % 10),
    placementType: slot.placementType,
    imageVariant: slot.placementType === "airborne" ? "flight" : "still",
    alternatives: [],
  };
}

function normalizedPlacement(species, index) {
  const configured = workingPlacements[species.species_name];
  if (configured) {
    return configured;
  }
  return defaultPlacement(species.species_name, index);
}

function candidatePlacements(placement) {
  return [placement, ...(placement.alternatives || []).map((alternative) => ({
    ...placement,
    ...alternative,
    anchor: alternative.anchor || placement.anchor,
    alternatives: placement.alternatives,
  }))];
}

function placementBox(placement) {
  const aspect = placement.placementType === "airborne" ? 0.7 : 0.86;
  const width = placement.size;
  const height = placement.size * aspect;
  return {
    left: placement.x - width * placement.anchor.x,
    right: placement.x + width * (1 - placement.anchor.x),
    top: placement.y - height * placement.anchor.y,
    bottom: placement.y + height * (1 - placement.anchor.y) + 0.045,
  };
}

function boxesOverlap(first, second) {
  const padding = 0.012;
  return !(
    first.right + padding < second.left ||
    second.right + padding < first.left ||
    first.bottom + padding < second.top ||
    second.bottom + padding < first.top
  );
}

function distributePlacements(speciesList) {
  const occupied = [];
  const resolved = new Map();
  speciesList.forEach((species, index) => {
    const base = normalizedPlacement(species, index);
    const candidates = candidatePlacements(base);
    let chosen = candidates.find((candidate) => {
      const box = placementBox(candidate);
      return !occupied.some((other) => boxesOverlap(box, other));
    });
    if (!chosen) {
      const fallback = defaultPlacement(species.species_name, index + 1);
      const fallbackCandidates = FALLBACK_SLOTS.map((slot) => ({
        ...fallback,
        ...slot,
        anchor: fallback.anchor,
      }));
      chosen = fallbackCandidates.find((candidate) => {
        const box = placementBox(candidate);
        return !occupied.some((other) => boxesOverlap(box, other));
      }) || candidates[0];
    }
    occupied.push(placementBox(chosen));
    resolved.set(species.species_name, chosen);
  });
  return resolved;
}

function setPlacementStyle(element, placement) {
  element.style.setProperty("--x", String(placement.x));
  element.style.setProperty("--y", String(placement.y));
  element.style.setProperty("--size", String(placement.size));
  element.style.setProperty("--anchor-x", String(placement.anchor.x));
  element.style.setProperty("--anchor-y", String(placement.anchor.y));
  element.style.setProperty("--rotation", `${placement.rotation || 0}deg`);
  element.style.setProperty("--bird-z", String(placement.zIndex || 20));
}

function labelPosition(placement, speciesName) {
  const label = placement.label || placementConfig.labels?.[speciesName] || {};
  const x = clamp(label.x ?? placement.x, 0.09, 0.91);
  const defaultOffset = placement.placementType === "ground" ? 0.055 : 0.075;
  const y = clamp(
    label.y ?? placement.y + (label.yOffset ?? defaultOffset),
    0.04,
    0.96,
  );
  return { x, y };
}

function renderScene() {
  if (!wallData) {
    return;
  }
  const resolved = distributePlacements(wallData.species);
  birdLayerEl.replaceChildren();
  labelLayerEl.replaceChildren();

  wallData.species.forEach((species, index) => {
    const placement = resolved.get(species.species_name);
    const bird = document.createElement("div");
    bird.className = "terrace-bird";
    bird.dataset.speciesName = species.species_name;
    bird.dataset.selected = String(species.species_name === selectedSpeciesName);
    bird.setAttribute("role", DEBUG_MODE ? "button" : "img");
    bird.setAttribute("aria-label", species.display_name);
    bird.tabIndex = DEBUG_MODE ? 0 : -1;
    setPlacementStyle(bird, placement);

    const imageUrl = imageVariant(species, placement);
    if (imageUrl) {
      const image = document.createElement("img");
      image.src = imageUrl;
      image.alt = "";
      image.loading = index < 6 ? "eager" : "lazy";
      image.decoding = "async";
      bird.append(image);
    } else {
      const fallback = document.createElement("div");
      fallback.className = "bird-fallback";
      fallback.textContent = initials(species.display_name);
      bird.append(fallback);
    }

    birdLayerEl.append(bird);
    if (wallData.show_names !== false) {
      const labelPositionValue = labelPosition(placement, species.species_name);
      const label = document.createElement("div");
      label.className = "bird-label";
      label.dataset.speciesName = species.species_name;
      label.style.setProperty("--label-x", String(labelPositionValue.x));
      label.style.setProperty("--label-y", String(labelPositionValue.y));
      const names = splitBirdNames(species.display_name);
      const labelText = document.createElement("span");
      labelText.textContent = names.primary;
      if (wallData.show_latin_names !== false && names.latin) {
        const latinName = document.createElement("small");
        latinName.textContent = names.latin;
        labelText.append(latinName);
      }
      label.append(labelText);
      labelLayerEl.append(label);
    }
  });
  updateDebugPanel();
}

function latestSpecies(speciesList) {
  return speciesList.reduce((latest, species) => {
    if (!latest) {
      return species;
    }
    return new Date(species.latest_analyzed_at) > new Date(latest.latest_analyzed_at)
      ? species
      : latest;
  }, null);
}

function updateStatus(data) {
  statusEl.hidden = data.show_footer === false;
  if (!data.species.length) {
    statusEl.textContent = "Ingen registreringer over det valgte filter endnu.";
    return;
  }
  const latest = latestSpecies(data.species);
  const updated = new Date(data.updated_at).toLocaleTimeString("da-DK", {
    hour: "2-digit",
    minute: "2-digit",
  });
  statusEl.textContent = `${data.species.length} arter · sidst hørt: ${splitDisplayName(
    latest.display_name,
  )} · opdateret ${updated}`;
}

async function loadTerrace() {
  try {
    const response = await fetch("/api/wall?stable_images=true", {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    wallData = await response.json();
    updateStatus(wallData);
    if (!selectedSpeciesName && wallData.species.length) {
      selectedSpeciesName = wallData.species[0].species_name;
    }
    renderScene();
  } catch (error) {
    statusEl.textContent = `Kunne ikke hente fugledata: ${error.message}`;
  }
}

function selectedSpecies() {
  return wallData?.species.find(
    (species) => species.species_name === selectedSpeciesName,
  );
}

function editablePlacement() {
  const species = selectedSpecies();
  if (!species) {
    return null;
  }
  if (!workingPlacements[species.species_name]) {
    const index = wallData.species.indexOf(species);
    workingPlacements[species.species_name] = defaultPlacement(
      species.species_name,
      index,
    );
  }
  return workingPlacements[species.species_name];
}

function updateDebugPanel() {
  if (!DEBUG_MODE || !wallData) {
    return;
  }
  const previousValue = speciesSelectEl.value;
  speciesSelectEl.replaceChildren();
  wallData.species.forEach((species) => {
    const option = document.createElement("option");
    option.value = species.species_name;
    option.textContent = splitDisplayName(species.display_name);
    speciesSelectEl.append(option);
  });
  speciesSelectEl.value = selectedSpeciesName || previousValue;
  const placement = editablePlacement();
  if (!placement) {
    coordinatesEl.textContent = "Ingen fugle at kalibrere.";
    return;
  }
  sizeInputEl.value = String(placement.size);
  rotationInputEl.value = String(placement.rotation || 0);
  sizeValueEl.value = `${Math.round(placement.size * 1000) / 10} %`;
  rotationValueEl.value = `${round(placement.rotation || 0, 1)}°`;
  coordinatesEl.textContent = JSON.stringify(
    { [selectedSpeciesName]: placement },
    null,
    2,
  );
}

function updateSelectedBird() {
  const placement = editablePlacement();
  const bird = birdLayerEl.querySelector(
    `[data-species-name="${CSS.escape(selectedSpeciesName)}"]`,
  );
  if (!placement || !bird) {
    return;
  }
  birdLayerEl.querySelectorAll(".terrace-bird").forEach((element) => {
    element.dataset.selected = String(element === bird);
  });
  setPlacementStyle(bird, placement);
  const label = labelLayerEl.querySelector(
    `[data-species-name="${CSS.escape(selectedSpeciesName)}"]`,
  );
  if (label) {
    const position = labelPosition(placement, selectedSpeciesName);
    label.style.setProperty("--label-x", String(position.x));
    label.style.setProperty("--label-y", String(position.y));
  }
  updateDebugPanel();
}

function startDrag(event) {
  if (!DEBUG_MODE) {
    return;
  }
  const bird = event.target.closest(".terrace-bird");
  if (!bird) {
    return;
  }
  selectedSpeciesName = bird.dataset.speciesName;
  const placement = editablePlacement();
  const rect = sceneEl.getBoundingClientRect();
  dragState = {
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: placement.x,
    startY: placement.y,
    width: rect.width,
    height: rect.height,
  };
  bird.setPointerCapture(event.pointerId);
  event.preventDefault();
  updateSelectedBird();
}

function moveDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) {
    return;
  }
  const placement = editablePlacement();
  placement.x = round(clamp(
    dragState.startX + (event.clientX - dragState.startClientX) / dragState.width,
    0,
    1,
  ));
  placement.y = round(clamp(
    dragState.startY + (event.clientY - dragState.startClientY) / dragState.height,
    0,
    1,
  ));
  updateSelectedBird();
}

function endDrag(event) {
  if (dragState && event.pointerId === dragState.pointerId) {
    dragState = null;
  }
}

function moveWithKeyboard(event) {
  if (!DEBUG_MODE || !event.target.closest(".terrace-bird")) {
    return;
  }
  const directions = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  };
  if (!directions[event.key]) {
    return;
  }
  selectedSpeciesName = event.target.closest(".terrace-bird").dataset.speciesName;
  const placement = editablePlacement();
  const step = event.shiftKey ? 0.01 : 0.001;
  placement.x = round(clamp(placement.x + directions[event.key][0] * step, 0, 1));
  placement.y = round(clamp(placement.y + directions[event.key][1] * step, 0, 1));
  event.preventDefault();
  updateSelectedBird();
}

async function initialize() {
  try {
    const configResponse = await fetch(CONFIG_URL);
    if (!configResponse.ok) {
      throw new Error(`HTTP ${configResponse.status}`);
    }
    placementConfig = await configResponse.json();
    workingPlacements = structuredClone(placementConfig.species || {});
  } catch (error) {
    statusEl.textContent = `Kunne ikke hente placeringskonfiguration: ${error.message}`;
    return;
  }

  if (DEBUG_MODE) {
    sceneEl.dataset.debug = "true";
    debugPanelEl.hidden = false;
    birdLayerEl.addEventListener("pointerdown", startDrag);
    birdLayerEl.addEventListener("pointermove", moveDrag);
    birdLayerEl.addEventListener("pointerup", endDrag);
    birdLayerEl.addEventListener("pointercancel", endDrag);
    birdLayerEl.addEventListener("keydown", moveWithKeyboard);
    speciesSelectEl.addEventListener("change", () => {
      selectedSpeciesName = speciesSelectEl.value;
      updateSelectedBird();
    });
    sizeInputEl.addEventListener("input", () => {
      editablePlacement().size = round(Number(sizeInputEl.value));
      updateSelectedBird();
    });
    rotationInputEl.addEventListener("input", () => {
      editablePlacement().rotation = round(Number(rotationInputEl.value), 1);
      updateSelectedBird();
    });
    copyConfigEl.addEventListener("click", async () => {
      const output = JSON.stringify(
        { ...placementConfig, species: workingPlacements },
        null,
        2,
      );
      try {
        await navigator.clipboard.writeText(output);
        copyConfigEl.textContent = "Kopieret";
        window.setTimeout(() => {
          copyConfigEl.textContent = "Kopiér konfiguration";
        }, 1600);
      } catch {
        coordinatesEl.textContent = output;
        copyConfigEl.textContent = "Vises nedenfor";
      }
    });
  }

  await loadTerrace();
  window.setInterval(loadTerrace, REFRESH_MS);
}

initialize();
