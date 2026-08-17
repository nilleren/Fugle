const heroImageEl = document.querySelector("#species-hero-image");
const titleEl = document.querySelector("#species-title");
const latinEl = document.querySelector("#species-latin");
const detectionsEl = document.querySelector("#species-detections");
const recordingsEl = document.querySelector("#species-recordings");
const confidenceEl = document.querySelector("#species-confidence");
const firstEl = document.querySelector("#species-first");
const latestEl = document.querySelector("#species-latest");
const latestAudioEl = document.querySelector("#species-latest-audio");
const listenButtonEl = document.querySelector("#species-listen-button");
const historyToggleEl = document.querySelector("#species-history-toggle");
const historyListEl = document.querySelector("#species-history-list");
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

let selectedHour = null;
let selectedMonth = null;
let currentSpeciesName = null;

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

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function formatClock(date) {
  return date.toLocaleTimeString("da-DK", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function daysBetween(fromDate, toDate) {
  return Math.floor((startOfDay(toDate) - startOfDay(fromDate)) / 86400000);
}

function monthName(date) {
  return date.toLocaleDateString("da-DK", { month: "long" });
}

function monthNameWithYear(date) {
  const now = new Date();
  const year = date.getFullYear() === now.getFullYear() ? "" : ` ${date.getFullYear()}`;
  return `${monthName(date)}${year}`;
}

function startOfWeek(date) {
  const day = date.getDay() || 7;
  const weekStart = startOfDay(date);
  weekStart.setDate(weekStart.getDate() - day + 1);
  return weekStart;
}

function formatRelativeHeardAt(prefix, value, confidence = null) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  const now = new Date();
  const daysAgo = daysBetween(date, now);
  const clock = formatClock(date);
  const confidenceText = confidence === null ? "" : ` (${formatPercent(confidence)})`;
  const recentWeekdays = [
    "i søndags",
    "i mandags",
    "i tirsdags",
    "i onsdags",
    "i torsdags",
    "i fredags",
    "i lørdags",
  ];

  if (daysAgo === 0) {
    return `${prefix} i dag kl. ${clock}${confidenceText}`;
  }
  if (daysAgo === 1) {
    return `${prefix} i går kl. ${clock}${confidenceText}`;
  }
  if (daysAgo > 1 && daysAgo <= 7) {
    return `${prefix} ${recentWeekdays[date.getDay()]} kl. ${clock}${confidenceText}`;
  }

  const includeYear = now.getTime() - date.getTime() >= 365 * 24 * 60 * 60 * 1000;
  const weekday = date.toLocaleDateString("da-DK", { weekday: "long" });
  const month = monthName(date);
  const year = includeYear ? ` ${date.getFullYear()}` : "";
  const dateText = `${weekday} d. ${date.getDate()}. ${month}${year}`;
  return `${prefix} ${dateText} kl. ${clock}${confidenceText}`;
}

function formatHistorySummary(match, groupType) {
  const date = new Date(match.analyzed_at);
  const now = new Date();
  const confidenceText = ` (${formatPercent(match.confidence)})`;

  if (groupType === "month") {
    return `Hørt i ${monthNameWithYear(date)}${confidenceText}`;
  }
  if (groupType === "week") {
    const weeksAgo = Math.max(1, Math.floor(daysBetween(startOfWeek(date), now) / 7));
    const weekText = weeksAgo === 1 ? "for 1 uge siden" : `for ${weeksAgo} uger siden`;
    return `Hørt ${weekText} kl. ${formatClock(date)}${confidenceText}`;
  }
  return formatRelativeHeardAt("Hørt", match.analyzed_at, match.confidence);
}

function formatHourNumber(hour) {
  return String(hour).padStart(2, "0");
}

function hourWindowText(hour) {
  const nextHour = (hour + 1) % 24;
  return `mellem kl. ${formatHourNumber(hour)} og ${formatHourNumber(nextHour)}`;
}

function activeFilterText() {
  const parts = [];
  if (selectedHour !== null) {
    parts.push(hourWindowText(selectedHour));
  }
  if (selectedMonth !== null) {
    parts.push(`i ${MONTH_NAMES[selectedMonth - 1]}`);
  }
  return parts.join(" og ");
}

function peakHourText(hourlyCounts) {
  if (selectedHour !== null) {
    return `Filtreret ${hourWindowText(selectedHour)} · klik igen for at vise alle timer`;
  }
  const maxCount = Math.max(...hourlyCounts, 0);
  if (maxCount === 0) {
    return selectedMonth === null ? "Ingen tydelig rytme" : `Ingen fund i ${MONTH_NAMES[selectedMonth - 1]}`;
  }
  const hour = hourlyCounts.indexOf(maxCount);
  return `Oftest hørt ${hourWindowText(hour)}`;
}

function peakMonthText(monthlyCounts) {
  if (selectedMonth !== null) {
    return `Filtreret i ${MONTH_NAMES[selectedMonth - 1]} · klik igen for at vise alle måneder`;
  }
  const maxCount = Math.max(...monthlyCounts, 0);
  if (maxCount === 0) {
    return selectedHour === null ? "Ingen tydelig sæson" : `Ingen fund ${hourWindowText(selectedHour)}`;
  }
  const monthIndex = monthlyCounts.indexOf(maxCount);
  return `Oftest hørt i ${MONTH_NAMES[monthIndex]}`;
}

function renderHourChart(hourlyCounts) {
  const maxCount = Math.max(...hourlyCounts, 1);
  hourChartEl.replaceChildren();
  hourlyCounts.forEach((count, hour) => {
    const bar = document.createElement("button");
    bar.className = "hour-bar";
    bar.type = "button";
    bar.dataset.hour = String(hour);
    bar.dataset.selected = String(selectedHour === hour);
    bar.setAttribute("aria-pressed", String(selectedHour === hour));
    bar.setAttribute("aria-label", `Kl. ${formatHourNumber(hour)}: ${formatNumber(count)} fund`);
    if ([6, 12, 18].includes(hour)) {
      bar.dataset.label = String(hour);
    }
    bar.title = `Kl. ${formatHourNumber(hour)}: ${formatNumber(count)} fund`;
    bar.style.setProperty("--height", `${(count / maxCount) * 100}%`);
    bar.addEventListener("click", () => {
      selectedHour = selectedHour === hour ? null : hour;
      loadSpecies();
    });
    hourChartEl.append(bar);
  });
}

function renderYearChart(monthlyCounts) {
  const maxCount = Math.max(...monthlyCounts, 1);
  yearChartEl.replaceChildren();
  monthlyCounts.forEach((count, index) => {
    const month = index + 1;
    const item = document.createElement("button");
    item.className = "year-month";
    item.type = "button";
    item.dataset.month = String(month);
    item.dataset.selected = String(selectedMonth === month);
    item.setAttribute("aria-pressed", String(selectedMonth === month));
    item.setAttribute("aria-label", `${MONTH_NAMES[index]}: ${formatNumber(count)} fund`);
    item.innerHTML = `
      <div class="year-bar"></div>
      <span>${MONTH_LABELS[index]}</span>
    `;
    item.querySelector(".year-bar").style.setProperty(
      "--height",
      `${(count / maxCount) * 100}%`,
    );
    item.title = `${MONTH_NAMES[index]}: ${formatNumber(count)} fund`;
    item.addEventListener("click", () => {
      selectedMonth = selectedMonth === month ? null : month;
      loadSpecies();
    });
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

function historyGroupForMatch(match) {
  const date = new Date(match.analyzed_at);
  const daysAgo = daysBetween(date, new Date());

  if (daysAgo > 62) {
    return {
      key: `month-${date.getFullYear()}-${date.getMonth()}`,
      plusLabel: `observationer i ${monthNameWithYear(date)}`,
      type: "month",
    };
  }
  if (daysAgo > 31) {
    const weekStart = startOfWeek(date);
    return {
      key: `week-${weekStart.toISOString().slice(0, 10)}`,
      plusLabel: "observationer denne uge",
      type: "week",
    };
  }
  return {
    key: `day-${date.toISOString().slice(0, 10)}`,
    plusLabel: "observationer denne dag",
    type: "day",
  };
}

function groupMatchHistory(history) {
  const groupsByKey = new Map();

  history.forEach((match) => {
    const groupInfo = historyGroupForMatch(match);
    if (!groupsByKey.has(groupInfo.key)) {
      groupsByKey.set(groupInfo.key, {
        matches: [],
        plusLabel: groupInfo.plusLabel,
        type: groupInfo.type,
      });
    }
    groupsByKey.get(groupInfo.key).matches.push(match);
  });

  return Array.from(groupsByKey.values());
}

function renderMatchHistory(species) {
  const history = Array.isArray(species.match_history)
    ? species.match_history
    : [];
  const groups = groupMatchHistory(history);

  historyListEl.replaceChildren();
  historyListEl.hidden = true;
  historyToggleEl.textContent = "Vis historik";
  historyToggleEl.setAttribute("aria-expanded", "false");
  historyToggleEl.hidden = history.length <= 2;

  groups.forEach((group, groupIndex) => {
    const [primaryMatch, ...extraMatches] = group.matches;
    const item = document.createElement("li");
    item.className = "species-history-item";

    const row = document.createElement("div");
    row.className = "species-history-row";

    const summary = document.createElement("span");
    summary.textContent = formatHistorySummary(primaryMatch, group.type);
    row.append(summary);

    if (extraMatches.length > 0) {
      const detailsId = `species-history-details-${groupIndex}`;
      const toggle = document.createElement("button");
      toggle.className = "species-history-more";
      toggle.type = "button";
      toggle.setAttribute("aria-controls", detailsId);
      toggle.setAttribute("aria-expanded", "false");
      toggle.textContent = `+ ${extraMatches.length} ${group.plusLabel}`;

      const details = document.createElement("ol");
      details.className = "species-history-details";
      details.id = detailsId;
      details.hidden = true;

      extraMatches.forEach((match) => {
        const detailItem = document.createElement("li");
        detailItem.textContent = formatRelativeHeardAt(
          "Hørt",
          match.analyzed_at,
          match.confidence,
        );
        details.append(detailItem);
      });

      toggle.addEventListener("click", () => {
        const shouldShow = details.hidden;
        details.hidden = !shouldShow;
        toggle.setAttribute("aria-expanded", String(shouldShow));
      });

      row.append(toggle);
      item.append(row, details);
    } else {
      item.append(row);
    }

    historyListEl.append(item);
  });
}

function renderSpecies(data) {
  const species = data.species;
  const { primaryName, secondaryName } = splitDisplayName(species.display_name);
  titleEl.textContent = primaryName;
  document.title = `${primaryName} - ${data.site_title || "Fuglene i haven"}`;
  latinEl.textContent = secondaryName;
  detectionsEl.textContent = formatNumber(species.count);
  recordingsEl.textContent = formatNumber(species.recording_count);
  confidenceEl.textContent = species.count > 0 ? formatPercent(species.best_confidence) : "-";
  firstEl.textContent = formatRelativeHeardAt(
    "Først hørt",
    species.first_analyzed_at,
  );
  latestEl.textContent = formatRelativeHeardAt(
    "Senest hørt",
    species.latest_analyzed_at,
    species.latest_confidence,
  );
  if (species.latest_recording_url) {
    latestAudioEl.src = species.latest_recording_url;
    latestAudioEl.hidden = true;
    listenButtonEl.hidden = false;
  } else {
    latestAudioEl.removeAttribute("src");
    latestAudioEl.hidden = true;
    listenButtonEl.hidden = true;
  }

  const filterText = activeFilterText();
  peakHourEl.textContent = peakHourText(species.hourly_counts);
  peakMonthEl.textContent = peakMonthText(species.monthly_counts);
  if (filterText && species.count === 0) {
    latestEl.textContent = `Ingen observationer ${filterText}`;
    firstEl.textContent = "-";
  }
  if (updatedAtEl) {
    updatedAtEl.textContent = `Opdateret ${new Date(data.updated_at).toLocaleTimeString(
      "da-DK",
      { hour: "2-digit", minute: "2-digit" },
    )}`;
  }

  renderHeroImage(species);
  renderMatchHistory(species);
  renderHourChart(species.hourly_counts);
  renderYearChart(species.monthly_counts);
}

async function loadSpecies() {
  const params = new URLSearchParams(window.location.search);
  currentSpeciesName = params.get("species_name");
  if (!currentSpeciesName) {
    titleEl.textContent = "Ingen fugl valgt";
    return;
  }

  const apiParams = new URLSearchParams({
    species_name: currentSpeciesName,
    min_confidence: "0.05",
  });
  if (selectedHour !== null) {
    apiParams.set("hour", String(selectedHour));
  }
  if (selectedMonth !== null) {
    apiParams.set("month", String(selectedMonth));
  }

  try {
    const response = await fetch(`/api/stats/species?${apiParams.toString()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    renderSpecies(await response.json());
  } catch (error) {
    titleEl.textContent = "Kunne ikke hente fuglen";
    latinEl.textContent = error.message;
  }
}

historyToggleEl.addEventListener("click", () => {
  const shouldShow = historyListEl.hidden;
  historyListEl.hidden = !shouldShow;
  historyToggleEl.textContent = shouldShow ? "Skjul historik" : "Vis historik";
  historyToggleEl.setAttribute("aria-expanded", String(shouldShow));
});

listenButtonEl.addEventListener("click", async () => {
  if (!latestAudioEl.src) {
    return;
  }
  latestAudioEl.currentTime = 0;
  await latestAudioEl.play();
});

loadSpecies();