const heroImageEl = document.querySelector("#species-hero-image");
const titleEl = document.querySelector("#species-title");
const latinEl = document.querySelector("#species-latin");
const detectionsEl = document.querySelector("#species-detections");
const recordingsEl = document.querySelector("#species-recordings");
const confidenceEl = document.querySelector("#species-confidence");
const firstEl = document.querySelector("#species-first");
const latestEl = document.querySelector("#species-latest");
const hourChartEl = document.querySelector("#species-hour-chart");
const yearChartEl = document.querySelector("#species-year-chart");
const peakHourEl = document.querySelector("#species-peak-hour");
const peakMonthEl = document.querySelector("#species-peak-month");
const updatedAtEl = document.querySelector("#updated-at");

const MONTH_LABELS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "Maj",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Okt",
  "Nov",
  "Dec",
];
const MONTH_NAMES = [
  "januar",
  "februar",
  "marts",
  "april",
  "maj",
  "juni",
  "juli",
  "august",
  "september",
  "oktober",
  "november",
  "december",
];

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

function formatPercent(value) {
  return `${Math.round((value || 0) * 100)}%`;
}

function formatDate(value) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString("da-DK", {
    day: "2-digit",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
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

function peakMonthText(monthlyCounts) {
  const maxCount = Math.max(...monthlyCounts, 0);
  if (maxCount === 0) {
    return "Ingen tydelig sæson";
  }
  const monthIndex = monthlyCounts.indexOf(maxCount);
  return `Flest hørt i ${MONTH_NAMES[monthIndex]}`;
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
    bar.title = `Kl. ${formatHourNumber(hour)}: ${formatNumber(count)} fund`;
    bar.style.setProperty("--height", `${(count / maxCount) * 100}%`);
    hourChartEl.append(bar);
  });
}

function renderYearChart(monthlyCounts) {
  const maxCount = Math.max(...monthlyCounts, 1);
  yearChartEl.replaceChildren();
  monthlyCounts.forEach((count, index) => {
    const item = document.createElement("div");
    item.className = "year-month";
    item.innerHTML = `
      <div class="year-bar"></div>
      <span>${MONTH_LABELS[index]}</span>
    `;
    item.querySelector(".year-bar").style.setProperty(
      "--height",
      `${(count / maxCount) * 100}%`,
    );
    item.title = `${MONTH_LABELS[index]}: ${formatNumber(count)} fund`;
    yearChartEl.append(item);
  });
}

function renderHeroImage(species) {
  heroImageEl.replaceChildren();
  const imageVariants = Array.isArray(species.image_variants)
    ? species.image_variants
    : [];

  if (imageVariants.length > 0) {
    imageVariants.forEach((variant) => {
      const frame = document.createElement("div");
      frame.className = "species-hero-image-frame";
      const image = document.createElement("img");
      image.src = variant.url;
      image.alt = species.display_name;
      frame.append(image);
      heroImageEl.append(frame);
    });
    return;
  }

  const imageUrl = species.still_image_url || species.image_url;
  if (imageUrl) {
    const frame = document.createElement("div");
    frame.className = "species-hero-image-frame";
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = species.display_name;
    frame.append(image);
    heroImageEl.append(frame);
    return;
  }

  const fallback = document.createElement("div");
  fallback.className = "species-fallback";
  fallback.textContent = initials(species.display_name);
  heroImageEl.append(fallback);
}

function renderSpecies(data) {
  const species = data.species;
  const { primaryName, secondaryName } = splitDisplayName(species.display_name);
  titleEl.textContent = primaryName;
  document.title = `${primaryName} - ${data.site_title || "Fuglene i haven"}`;
  latinEl.textContent = secondaryName;
  detectionsEl.textContent = formatNumber(species.count);
  recordingsEl.textContent = formatNumber(species.recording_count);
  confidenceEl.textContent = formatPercent(species.best_confidence);
  firstEl.textContent = formatDate(species.first_analyzed_at);
  latestEl.textContent = formatDate(species.latest_analyzed_at);
  peakHourEl.textContent = peakHourText(species.hourly_counts);
  peakMonthEl.textContent = peakMonthText(species.monthly_counts);
  updatedAtEl.textContent = `Opdateret ${new Date(data.updated_at).toLocaleTimeString(
    "da-DK",
    { hour: "2-digit", minute: "2-digit" },
  )}`;

  renderHeroImage(species);
  renderHourChart(species.hourly_counts);
  renderYearChart(species.monthly_counts);
}

async function loadSpecies() {
  const params = new URLSearchParams(window.location.search);
  const speciesName = params.get("species_name");
  if (!speciesName) {
    titleEl.textContent = "Ingen fugl valgt";
    return;
  }

  try {
    const response = await fetch(
      `/api/stats/species?species_name=${encodeURIComponent(speciesName)}&min_confidence=0.05`,
    );
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    renderSpecies(await response.json());
  } catch (error) {
    titleEl.textContent = "Kunne ikke hente fuglen";
    latinEl.textContent = error.message;
  }
}

loadSpecies();
