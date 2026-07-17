const statusEl = document.querySelector("#status");
const countEl = document.querySelector("#detection-count");
const speciesCountEl = document.querySelector("#species-count");
const databaseEl = document.querySelector("#database-path");
const bodyEl = document.querySelector("#detections-body");
const emptyEl = document.querySelector("#empty-state");
const refreshButton = document.querySelector("#refresh-button");
const autoRefreshEl = document.querySelector("#auto-refresh");
const confidenceFilterEl = document.querySelector("#confidence-filter");
const confidenceValueEl = document.querySelector("#confidence-value");
const speciesSummaryEl = document.querySelector("#species-summary");
const stationStateEl = document.querySelector("#station-state");
const stationMessageEl = document.querySelector("#station-message");
const lastCycleEl = document.querySelector("#last-cycle");
const nextCycleEl = document.querySelector("#next-cycle");
const startSchedulerButton = document.querySelector("#start-scheduler-button");
const stopSchedulerButton = document.querySelector("#stop-scheduler-button");
const schedulerControlMessageEl = document.querySelector("#scheduler-control-message");
const configPathEl = document.querySelector("#config-path");
const configDeviceEl = document.querySelector("#config-device");
const configAudioEl = document.querySelector("#config-audio");
const configGeoEl = document.querySelector("#config-geo");
const configBirdnetEl = document.querySelector("#config-birdnet");
const configScheduleEl = document.querySelector("#config-schedule");
const configQuietEl = document.querySelector("#config-quiet");
const configDatabaseEl = document.querySelector("#config-database");
const audioDevicesStatusEl = document.querySelector("#audio-devices-status");
const audioDevicesListEl = document.querySelector("#audio-devices-list");
const testRecordingButton = document.querySelector("#test-recording-button");
const testRecordingStatusEl = document.querySelector("#test-recording-status");
const audioDeviceSaveStatusEl = document.querySelector("#audio-device-save-status");
const runtimeSettingsForm = document.querySelector("#runtime-settings-form");
const siteTitleSettingEl = document.querySelector("#site-title-setting");
const durationSettingEl = document.querySelector("#duration-setting");
const geoConfidenceSettingEl = document.querySelector("#geo-confidence-setting");
const quietStartSettingEl = document.querySelector("#quiet-start-setting");
const quietEndSettingEl = document.querySelector("#quiet-end-setting");
const wallMaxSpeciesSettingEl = document.querySelector("#wall-max-species-setting");
const wallRecentMinutesSettingEl = document.querySelector(
  "#wall-recent-minutes-setting",
);
const wallShowNamesSettingEl = document.querySelector("#wall-show-names-setting");
const wallShowLatinNamesSettingEl = document.querySelector(
  "#wall-show-latin-names-setting",
);
const wallShowFooterSettingEl = document.querySelector("#wall-show-footer-setting");
const wallShowShadowsSettingEl = document.querySelector("#wall-show-shadows-setting");
const wallSizeModeSettingEl = document.querySelector("#wall-size-mode-setting");
const saveRuntimeSettingsButton = document.querySelector(
  "#save-runtime-settings-button",
);
const resetDefaultSettingsButton = document.querySelector(
  "#reset-default-settings-button",
);
const runtimeSettingsStatusEl = document.querySelector("#runtime-settings-status");

const REFRESH_INTERVAL_MS = 10000;
let refreshTimer = null;

function formatPercent(value) {
  return `${Math.round(value * 1000) / 10}%`;
}

function formatSeconds(value) {
  return `${Number(value).toFixed(1)} sek.`;
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString("da-DK");
}

function formatGeo(config) {
  if (!config.use_geo) {
    return "Geografi slået fra";
  }
  return `${config.latitude}, ${config.longitude}`;
}

function formatWeek(value) {
  return Number(value) === 0 ? "aktuel uge" : `uge ${value}`;
}

function formatInterval(seconds) {
  const value = Number(seconds);
  if (value < 60) {
    return `${value} sek.`;
  }
  return `${value / 60} min.`;
}

function formatWallSizeMode(value) {
  if (value === "equal") {
    return "ens størrelse";
  }
  if (value === "rare") {
    return "sjældnest størst";
  }
  return "flest fund størst";
}

function formatState(value) {
  const labels = {
    error: "Fejl",
    quiet: "Natpause",
    running_cycle: "Optager/analyserer",
    starting: "Starter",
    stopped: "Stoppet",
    unknown: "Ukendt",
    waiting: "Venter",
  };
  return labels[value] || value;
}

function renderRows(detections) {
  bodyEl.replaceChildren();

  for (const detection of detections) {
    const row = document.createElement("tr");

    const species = document.createElement("td");
    species.innerHTML = `<span class="species"></span>`;
    species.querySelector(".species").textContent = detection.display_name;

    const confidence = document.createElement("td");
    confidence.className = "confidence";
    confidence.textContent = formatPercent(detection.confidence);

    const segment = document.createElement("td");
    segment.textContent = `${formatSeconds(detection.start_time)} - ${formatSeconds(
      detection.end_time,
    )}`;

    const analyzed = document.createElement("td");
    analyzed.textContent = detection.analyzed_at;

    const recording = document.createElement("td");
    recording.className = "muted";
    recording.textContent = detection.recording_name;

    row.append(species, confidence, segment, analyzed, recording);
    bodyEl.append(row);
  }
}

function renderSpeciesSummary(speciesSummary) {
  speciesSummaryEl.replaceChildren();
  speciesCountEl.textContent = String(speciesSummary.length);

  if (speciesSummary.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Ingen arter over filteret endnu.";
    speciesSummaryEl.append(empty);
    return;
  }

  for (const species of speciesSummary) {
    const item = document.createElement("article");
    item.className = "species-card";

    const name = document.createElement("strong");
    name.textContent = species.display_name;

    const meta = document.createElement("span");
    meta.textContent = `${species.count} fund - bedste ${formatPercent(
      species.best_confidence,
    )}`;

    item.append(name, meta);
    speciesSummaryEl.append(item);
  }
}

function renderAudioDevices(data) {
  audioDevicesListEl.replaceChildren();
  audioDevicesStatusEl.textContent = `${data.count} input-enhed(er) fundet`;

  if (data.devices.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Ingen mikrofoner fundet.";
    audioDevicesListEl.append(empty);
    return;
  }

  for (const device of data.devices) {
    const item = document.createElement("article");
    item.className = "audio-device";
    if (device.configured) {
      item.dataset.configured = "true";
    }

    const name = document.createElement("strong");
    name.textContent = `[${device.index}] ${device.name}`;

    const meta = document.createElement("span");
    meta.textContent = `${device.host_api}, ${device.max_input_channels} kanal(er), ${device.default_samplerate} Hz`;

    const badge = document.createElement("span");
    badge.className = "device-badge";
    badge.textContent = device.configured ? "Valgt i config" : "Input";

    const chooseButton = document.createElement("button");
    chooseButton.type = "button";
    chooseButton.className = "choose-device-button secondary";
    chooseButton.dataset.deviceIndex = String(device.index);
    chooseButton.disabled = device.configured;
    chooseButton.textContent = device.configured ? "Valgt" : "Vælg";

    item.append(name, meta, badge, chooseButton);
    audioDevicesListEl.append(item);
  }
}

function updateConfidenceLabel() {
  confidenceValueEl.textContent = formatPercent(Number(confidenceFilterEl.value));
}

async function loadDetections() {
  statusEl.classList.remove("error");
  statusEl.textContent = "Indlæser seneste detektioner...";
  refreshButton.disabled = true;

  try {
    const params = new URLSearchParams({
      limit: "50",
      min_confidence: confidenceFilterEl.value,
    });
    const response = await fetch(`/api/detections?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    countEl.textContent = String(data.count);
    databaseEl.textContent = data.database;
    renderSpeciesSummary(data.species_summary);
    renderRows(data.detections);
    emptyEl.hidden = data.detections.length > 0;
    statusEl.textContent = `Klar - senest opdateret ${new Date().toLocaleTimeString(
      "da-DK",
    )}`;
  } catch (error) {
    statusEl.classList.add("error");
    statusEl.textContent = `Kunne ikke hente detektioner: ${error.message}`;
  } finally {
    refreshButton.disabled = false;
  }
}

async function loadStationStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const status = await response.json();
    stationStateEl.textContent = formatState(status.state);
    stationStateEl.dataset.state = status.state;
    stationMessageEl.textContent = status.last_error || status.message;
    lastCycleEl.textContent = formatDateTime(status.last_cycle_finished_at);
    nextCycleEl.textContent = formatDateTime(status.next_cycle_at);
    startSchedulerButton.disabled = status.scheduler_process_running;
    stopSchedulerButton.disabled = !status.scheduler_process_running;
  } catch (error) {
    stationStateEl.textContent = "Fejl";
    stationStateEl.dataset.state = "error";
    stationMessageEl.textContent = `Kunne ikke hente status: ${error.message}`;
    lastCycleEl.textContent = "-";
    nextCycleEl.textContent = "-";
    startSchedulerButton.disabled = false;
    stopSchedulerButton.disabled = true;
  }
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const config = await response.json();

    siteTitleSettingEl.value = config.site.title;
    configPathEl.textContent = config.config_path;
    configDeviceEl.textContent = `Device ${config.audio.device}`;
    configAudioEl.textContent = `${config.audio.duration_seconds} sek. ved ${
      config.audio.sample_rate
    } Hz til ${config.audio.output_dir}`;
    durationSettingEl.value = String(config.audio.duration_seconds);
    configGeoEl.textContent = formatGeo(config.birdnet);
    configBirdnetEl.textContent = `${formatWeek(
      config.birdnet.week,
    )}, geo confidence ${formatPercent(config.birdnet.geo_min_confidence)}`;
    geoConfidenceSettingEl.value = String(config.birdnet.geo_min_confidence);
    configScheduleEl.textContent = `${formatInterval(
      config.schedule.first_phase_interval_seconds,
    )} -> ${formatInterval(
      config.schedule.second_phase_interval_seconds,
    )} -> ${formatInterval(config.schedule.steady_interval_seconds)}`;
    configQuietEl.textContent = `Natpause ${config.schedule.quiet_start} - ${config.schedule.quiet_end}`;
    quietStartSettingEl.value = config.schedule.quiet_start;
    quietEndSettingEl.value = config.schedule.quiet_end;
    wallMaxSpeciesSettingEl.value = String(config.wall.max_species);
    wallRecentMinutesSettingEl.value = String(config.wall.recent_minutes);
    wallShowNamesSettingEl.checked = config.wall.show_names;
    wallShowLatinNamesSettingEl.checked = config.wall.show_latin_names;
    wallShowFooterSettingEl.checked = config.wall.show_footer;
    wallShowShadowsSettingEl.checked = config.wall.show_shadows;
    wallSizeModeSettingEl.value = config.wall.size_mode;
    configDatabaseEl.textContent = config.database.path;
  } catch (error) {
    configPathEl.textContent = `Kunne ikke hente konfiguration: ${error.message}`;
    configDeviceEl.textContent = "-";
    configAudioEl.textContent = "-";
    configGeoEl.textContent = "-";
    configBirdnetEl.textContent = "-";
    configScheduleEl.textContent = "-";
    configQuietEl.textContent = "-";
    configDatabaseEl.textContent = "-";
  }
}

async function loadAudioDevices() {
  try {
    const response = await fetch("/api/audio/devices");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    renderAudioDevices(data);
  } catch (error) {
    audioDevicesStatusEl.textContent = `Kunne ikke hente mikrofoner: ${error.message}`;
    audioDevicesListEl.replaceChildren();
  }
}

async function chooseAudioDevice(deviceIndex) {
  audioDeviceSaveStatusEl.classList.remove("error");
  audioDeviceSaveStatusEl.textContent = `Gemmer device ${deviceIndex}...`;

  for (const button of document.querySelectorAll(".choose-device-button")) {
    button.disabled = true;
  }

  try {
    const response = await fetch("/api/config/audio-device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: Number(deviceIndex) }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || `HTTP ${response.status}`);
    }

    audioDeviceSaveStatusEl.textContent = `${result.message} Device ${result.device}: ${result.microphone.name}`;
    await loadConfig();
    await loadAudioDevices();
  } catch (error) {
    audioDeviceSaveStatusEl.classList.add("error");
    audioDeviceSaveStatusEl.textContent = `Kunne ikke gemme mikrofonvalg: ${error.message}`;
    await loadAudioDevices();
  }
}

async function saveRuntimeSettings(event) {
  event.preventDefault();
  saveRuntimeSettingsButton.disabled = true;
  runtimeSettingsStatusEl.classList.remove("error");
  runtimeSettingsStatusEl.textContent = "Gemmer indstillinger...";

  try {
    const siteTitle = siteTitleSettingEl.value.trim();
    const durationSeconds = Number(durationSettingEl.value);
    const geoMinConfidence = Number(geoConfidenceSettingEl.value);
    const quietStart = quietStartSettingEl.value;
    const quietEnd = quietEndSettingEl.value;
    const wallMaxSpecies = Number(wallMaxSpeciesSettingEl.value);
    const wallRecentMinutes = Number(wallRecentMinutesSettingEl.value);
    const wallShowNames = wallShowNamesSettingEl.checked;
    const wallShowLatinNames = wallShowLatinNamesSettingEl.checked;
    const wallShowFooter = wallShowFooterSettingEl.checked;
    const wallShowShadows = wallShowShadowsSettingEl.checked;
    const wallSizeMode = wallSizeModeSettingEl.value;
    const response = await fetch("/api/config/runtime-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_title: siteTitle,
        duration_seconds: durationSeconds,
        geo_min_confidence: geoMinConfidence,
        quiet_start: quietStart,
        quiet_end: quietEnd,
        wall_max_species: wallMaxSpecies,
        wall_recent_minutes: wallRecentMinutes,
        wall_show_names: wallShowNames,
        wall_show_latin_names: wallShowLatinNames,
        wall_show_footer: wallShowFooter,
        wall_show_shadows: wallShowShadows,
        wall_size_mode: wallSizeMode,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || `HTTP ${response.status}`);
    }

    runtimeSettingsStatusEl.textContent = `${result.message} Titel "${
      result.site_title
    }", ${
      result.duration_seconds
    } sek., ${formatPercent(result.geo_min_confidence)} confidence, natpause ${
      result.quiet_start
    }-${result.quiet_end}, væg: ${result.wall_max_species} fugle / ${
      result.wall_recent_minutes
    } min., navne ${result.wall_show_names ? "til" : "fra"}, bundtekst ${
      result.wall_show_footer ? "til" : "fra"
    }, latin ${result.wall_show_latin_names ? "til" : "fra"}, skygge ${
      result.wall_show_shadows ? "til" : "fra"
    }, ${formatWallSizeMode(
      result.wall_size_mode,
    )}.`;
    await loadConfig();
  } catch (error) {
    runtimeSettingsStatusEl.classList.add("error");
    runtimeSettingsStatusEl.textContent = `Kunne ikke gemme indstillinger: ${error.message}`;
  } finally {
    saveRuntimeSettingsButton.disabled = false;
  }
}

async function resetDefaultSettings() {
  const confirmed = window.confirm(
    "Vil du nulstille dashboardets indstillinger til default-konfigurationen?",
  );
  if (!confirmed) {
    return;
  }

  resetDefaultSettingsButton.disabled = true;
  runtimeSettingsStatusEl.classList.remove("error");
  runtimeSettingsStatusEl.textContent = "Nulstiller indstillinger...";

  try {
    const response = await fetch("/api/config/reset-defaults", { method: "POST" });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || `HTTP ${response.status}`);
    }

    runtimeSettingsStatusEl.textContent = result.message;
    await loadConfig();
    await loadAudioDevices();
  } catch (error) {
    runtimeSettingsStatusEl.classList.add("error");
    runtimeSettingsStatusEl.textContent = `Kunne ikke nulstille indstillinger: ${error.message}`;
  } finally {
    resetDefaultSettingsButton.disabled = false;
  }
}

async function testRecording() {
  testRecordingButton.disabled = true;
  testRecordingStatusEl.classList.remove("error");
  testRecordingStatusEl.textContent = "Optager 3 sekunder...";

  try {
    const response = await fetch("/api/audio/test-recording", { method: "POST" });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || `HTTP ${response.status}`);
    }

    testRecordingStatusEl.textContent = `${result.message} ${
      result.recording_name
    } (${result.duration_seconds} sek., ${result.sample_rate} Hz, device ${
      result.microphone.index
    })`;
  } catch (error) {
    testRecordingStatusEl.classList.add("error");
    testRecordingStatusEl.textContent = `Testoptagelse fejlede: ${error.message}`;
  } finally {
    testRecordingButton.disabled = false;
  }
}

async function controlScheduler(action) {
  startSchedulerButton.disabled = true;
  stopSchedulerButton.disabled = true;
  schedulerControlMessageEl.textContent =
    action === "start" ? "Starter station..." : "Stopper station...";

  try {
    const response = await fetch(`/api/scheduler/${action}`, { method: "POST" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const result = await response.json();
    schedulerControlMessageEl.textContent = result.message;
  } catch (error) {
    schedulerControlMessageEl.textContent = `Kunne ikke styre scheduler: ${error.message}`;
  } finally {
    loadStationStatus();
  }
}

function scheduleAutoRefresh() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }

  if (autoRefreshEl.checked) {
    refreshTimer = window.setInterval(() => {
      loadDetections();
      loadStationStatus();
    }, REFRESH_INTERVAL_MS);
  }
}

refreshButton.addEventListener("click", () => {
  loadDetections();
  loadStationStatus();
  loadConfig();
  loadAudioDevices();
});
startSchedulerButton.addEventListener("click", () => controlScheduler("start"));
stopSchedulerButton.addEventListener("click", () => controlScheduler("stop"));
testRecordingButton.addEventListener("click", testRecording);
runtimeSettingsForm.addEventListener("submit", saveRuntimeSettings);
resetDefaultSettingsButton.addEventListener("click", resetDefaultSettings);
audioDevicesListEl.addEventListener("click", (event) => {
  const button = event.target.closest(".choose-device-button");
  if (!button) {
    return;
  }
  chooseAudioDevice(button.dataset.deviceIndex);
});
autoRefreshEl.addEventListener("change", scheduleAutoRefresh);
confidenceFilterEl.addEventListener("input", () => {
  updateConfidenceLabel();
  loadDetections();
});

updateConfidenceLabel();
scheduleAutoRefresh();
loadDetections();
loadStationStatus();
loadConfig();
loadAudioDevices();
