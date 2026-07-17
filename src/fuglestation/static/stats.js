const totalDetectionsEl = document.querySelector("#total-detections");
const statsTitleEl = document.querySelector("#stats-title");
const totalSpeciesEl = document.querySelector("#total-species");
const totalRecordingsEl = document.querySelector("#total-recordings");
const hourChartEl = document.querySelector("#hour-chart");
const peakHourEl = document.querySelector("#peak-hour");
const speciesWindowEl = document.querySelector("#species-window");
const topSpeciesEl = document.querySelector("#top-species");
const updatedAtEl = document.querySelector("#updated-at");
const periodButtons = Array.from(document.querySelectorAll(".period-picker button"));

const STATS_REFRESH_MS = 60000;
const TOP_SPECIES_COLLAPSED_COUNT = 5;
let showAllTopSpecies = false;
let selectedDays = 30;

const PERIOD_LABELS = new Map([
  [1, "Seneste døgn"],
  [7, "Seneste uge"],
  [30, "Seneste måned"],
  [0, "Al tid"],
]);

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

function initials(displayName) {
  return displayName
    .split("/")
    .map((part) => part.trim()[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function formatNumber(value) {
  return new Intl.NumberFormat("da-DK").format(value || 0);
}

function formatHour(hour) {
  return `${String(hour).padStart(2, "0")}.00`;
}

function formatHourNumber(hour) {
  return String(hour).padStart(2, "0");
}

function peakHourText(hourlyCounts) {
  const maxCount = Math.max(...hourlyCounts, 0);
  if (maxCount === 0) {
    return "Ingen tydelig rytme";
  }
  const hour = hourlyCounts.indexOf(maxCount);
  const nextHour = (hour + 1) % 24;
  return `Flest hørt mellem kl. ${formatHourNumber(hour)} og ${formatHourNumber(nextHour)}`;
}

function renderHourChart(hourlyCounts) {
  const maxCount = Math.max(...hourlyCounts, 1);
  hourChartEl.replaceChildren();
  hourlyCounts.forEach((count, hour) => {
    const bar = document.createElement("div");
    bar.className = "hour-bar";
    bar.dataset.hour = String(hour);
    if ([6, 12, 18].includes(hour)) {
      bar.dataset.label = String(hour);
    }
    bar.title = `${formatHour(hour)}: ${formatNumber(count)} fund`;
    bar.style.setProperty("--height", `${(count / maxCount) * 100}%`);
    hourChartEl.append(bar);
  });
}

function renderTopSpecies(speciesList) {
  topSpeciesEl.replaceChildren();
  const visibleCount = showAllTopSpecies
    ? speciesList.length
    : TOP_SPECIES_COLLAPSED_COUNT;
  const topSpecies = speciesList.slice(0, visibleCount);
  const maxCount = Math.max(...topSpecies.map((species) => species.count), 1);
  topSpecies.forEach((species) => {
    const { primaryName } = splitDisplayName(species.display_name);
    const row = document.createElement("a");
    row.className = "top-row";
    row.href = `/stats/species?species_name=${encodeURIComponent(species.species_name)}`;
    row.innerHTML = `
      <div class="top-row-main">
        <div class="top-row-image"></div>
        <div class="top-row-copy">
          <div class="top-row-label">
            <span class="top-row-name"></span>
            <span class="top-row-count">${formatNumber(species.count)}</span>
          </div>
          <div class="top-row-track">
            <div class="top-row-fill"></div>
          </div>
        </div>
      </div>
    `;
    const imageWrap = row.querySelector(".top-row-image");
    const imageUrl = species.still_image_url || species.image_url;
    if (imageUrl) {
      const image = document.createElement("img");
      image.src = imageUrl;
      image.alt = species.display_name;
      imageWrap.append(image);
    } else {
      imageWrap.textContent = initials(species.display_name);
    }
    row.querySelector(".top-row-name").textContent = primaryName;
    row.querySelector(".top-row-fill").style.width = `${(species.count / maxCount) * 100}%`;
    topSpeciesEl.append(row);
  });

  if (speciesList.length > TOP_SPECIES_COLLAPSED_COUNT) {
    const toggleButton = document.createElement("button");
    toggleButton.className = "show-more-button";
    toggleButton.type = "button";
    toggleButton.textContent = showAllTopSpecies ? "Vis færre" : "Vis flere";
    toggleButton.addEventListener("click", () => {
      showAllTopSpecies = !showAllTopSpecies;
      renderTopSpecies(speciesList);
    });
    topSpeciesEl.append(toggleButton);
  }
}

function renderStats(data) {
  statsTitleEl.textContent = data.site_title || "Fuglene i haven";
  document.title = data.site_title || "Fuglestatistik";
  totalDetectionsEl.textContent = formatNumber(data.overview.detection_count);
  totalSpeciesEl.textContent = formatNumber(data.overview.species_count);
  totalRecordingsEl.textContent = formatNumber(data.overview.recording_count);
  peakHourEl.textContent = peakHourText(data.hourly_counts);
  speciesWindowEl.hidden = true;
  speciesWindowEl.textContent = "";
  updatedAtEl.textContent = `Opdateret ${new Date(data.updated_at).toLocaleTimeString(
    "da-DK",
    { hour: "2-digit", minute: "2-digit" },
  )}`;

  renderHourChart(data.hourly_counts);
  renderTopSpecies(data.species);
}

async function loadStats() {
  try {
    const response = await fetch(
      `/api/stats?days=${selectedDays}&limit=24&min_confidence=0.05`,
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    renderStats(await response.json());
  } catch (error) {
    speciesWindowEl.hidden = false;
    speciesWindowEl.textContent = `Kunne ikke hente statistik: ${error.message}`;
  }
}

periodButtons.forEach((button) => {
  button.addEventListener("click", () => {
    selectedDays = Number(button.dataset.days);
    showAllTopSpecies = false;
    periodButtons.forEach((periodButton) => {
      periodButton.dataset.active = String(periodButton === button);
    });
    loadStats();
  });
});

loadStats();
window.setInterval(loadStats, STATS_REFRESH_MS);
