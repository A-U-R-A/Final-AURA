/* ============================================================
   AURA — app.js
   Single-file SPA controller
   ============================================================ */

"use strict";

// ── State ────────────────────────────────────────────────────────────────────
// ── Application state ─────────────────────────────────────────────────────────
// Single source of truth for all UI state. Mutated by WebSocket messages and
// REST responses; read by render functions whenever the page is refreshed.
const state = {
  config: null,                // /api/config payload (locations, ranges, units, etc.)
  locationStates: {},          // {location: {is_anomalous, active_fault, latched}}
  dqnRecs: {},                 // {location: {action, action_index, confidence}} — from WS ticks
  locationData: {},            // {location: {param: value}} — latest sensor readings
  ws: null,                    // active WebSocket instance
  wsConnected: false,
  activePage: "twin",          // currently visible tab
  detailChart: null,           // Chart.js instance on the Detail page (destroyed on each load)
  detailLive: true,            // whether live WS ticks push new points to the detail chart
};

// ── DOM references ───────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Boot ──────────────────────────────────────────────────────────────────────
// Called once on DOMContentLoaded. Fetches static config then initialises every
// page section before opening the WebSocket for live data.
async function boot() {
  try {
    state.config = await apiFetch("/api/config");
  } catch (e) {
    showToast("Failed to reach backend — is the server running?", "error");
    return;
  }

  setupTabs();
  populateSelects();
  buildDigitalTwin();
  buildDashboard();
  buildDetail();
  buildTrends();
  buildAlerts();
  buildAnalyst();
  buildMaintenance();
  connectWebSocket();

  // Load initial location states before any WS tick arrives
  await refreshLocationStates();
  refreshAlertBadge();
  // Poll alert count every 30 s as a fallback in case a WS alert message is missed
  setInterval(refreshAlertBadge, 30_000);
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const page = btn.dataset.page;
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      $(`page-${page}`).classList.add("active");
      state.activePage = page;
      if (page === "dashboard") refreshDashboard();
      if (page === "trends")    refreshTrends();
      if (page === "alerts")    refreshAlerts();
      if (page === "analyst")     refreshAnalystLabels();
      if (page === "maintenance") refreshMaintenance();
    });
  });
}

// ── Alert badge (tab notification dot) ───────────────────────────────────────
async function refreshAlertBadge() {
  try {
    const { unacknowledged } = await apiFetch("/api/alerts/count");
    const badge = $("alert-badge");
    if (unacknowledged > 0) {
      badge.textContent = unacknowledged > 99 ? "99+" : unacknowledged;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  } catch (_) {}
}

// ── Populate selects with config data ────────────────────────────────────────
function populateSelects() {
  const { locations, faults } = state.config;

  // Twin sidebar
  populateSelect("inject-location", locations);
  populateSelect("inject-fault", faults);

  // Dashboard
  $("btn-dashboard-refresh").addEventListener("click", refreshDashboard);

  // Detail
  populateSelect("detail-location", locations);
  $("detail-location").addEventListener("change", onDetailLocationChange);
  onDetailLocationChange();  // populate parameters

  // Trends
  populateSelect("trends-location", locations);

  // Analyst
  populateSelect("analyst-location", locations);
}

function populateSelect(id, items) {
  const el = $(id);
  el.innerHTML = "";
  items.forEach(item => {
    const opt = document.createElement("option");
    opt.value = item;
    opt.textContent = item;
    el.appendChild(opt);
  });
}

// ── WebSocket ────────────────────────────────────────────────────────────────
// Opens a persistent connection to /ws/live. On disconnect, schedules a
// reconnection attempt after 3 s. The 25-second keepalive ping prevents idle
// proxies (e.g. nginx) from closing the connection due to inactivity timeout.
function connectWebSocket() {
  const wsUrl = `ws://${location.host}/ws/live`;
  state.ws = new WebSocket(wsUrl);

  state.ws.addEventListener("open", () => {
    state.wsConnected = true;
    $("ws-indicator").className = "ws-indicator connected";
    $("ws-indicator").title = "WebSocket connected";
  });

  state.ws.addEventListener("close", () => {
    state.wsConnected = false;
    $("ws-indicator").className = "ws-indicator disconnected";
    // Auto-reconnect after 3 s — the server sends the current state snapshot on connect
    setTimeout(connectWebSocket, 3000);
  });

  state.ws.addEventListener("error", () => {
    state.wsConnected = false;
    $("ws-indicator").className = "ws-indicator disconnected";
  });

  state.ws.addEventListener("message", e => {
    try {
      const msg = JSON.parse(e.data);
      handleWsMessage(msg);
    } catch (_) {}
  });

  // Keepalive ping every 25 s — avoids idle proxy timeouts
  setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send("ping");
    }
  }, 25000);
}

// ── WebSocket message router ──────────────────────────────────────────────────
// Three server-sent message types:
//   "state"  — full location snapshot (sent once on connect + after each full tick batch)
//   "alert"  — debounced/latched fault alert (triggers toast + optional latch popup)
//   "tick"   — per-location per-tick data (sensor values, IF/RF labels, DQN recommendation)
function handleWsMessage(msg) {
  if (msg.type === "state") {
    // Replace entire locationStates map and refresh all state-dependent UI
    state.locationStates = msg.locations || {};
    updateSystemStatusBadge();
    updateTwinIndicators();
    updateLocationList();

  } else if (msg.type === "alert") {
    const faultText = msg.fault_type || "Anomaly";
    const probText  = msg.top_prob ? ` (${(msg.top_prob * 100).toFixed(0)}%)` : "";
    showToast(`⚠ ${msg.severity}: ${faultText}${probText} @ ${msg.location}`, "error");
    refreshAlertBadge();
    // Latch alerts get a modal popup (and optional alarm) in addition to the toast
    if (msg.latched) {
      showLatchPopup(msg.location, faultText, msg.top_prob || 0);
    }

  } else if (msg.type === "tick") {
    // Cache latest sensor readings and DQN recommendation for this location
    if (msg.data) state.locationData[msg.location] = msg.data;
    if (msg.dqn)  state.dqnRecs[msg.location] = msg.dqn;
    // Merge fault/anomaly state so dashboard cards stay accurate without waiting for "state"
    if (!state.locationStates[msg.location]) state.locationStates[msg.location] = {};
    state.locationStates[msg.location].active_fault = msg.active_fault || null;
    state.locationStates[msg.location].is_anomalous = msg.if_label === -1;

    // Only update dashboard cards in-place when the dashboard is visible — avoids
    // touching the DOM for pages the user isn't looking at
    if (state.activePage === "dashboard") {
      updateLocationCardInPlace(msg.location, msg.data, msg.dqn, msg.if_label);
    }
    // Push new point to the live-detail chart if the selected location + param match
    if (state.activePage === "detail" && state.detailLive) {
      const loc = $("detail-location").value;
      const param = $("detail-parameter").value;
      if (msg.location === loc && msg.data && param in msg.data) {
        pushDetailPoint(msg.timestamp, msg.data[param], msg.if_label === -1);
      }
    }
    // Prepend to the anomaly labels sidebar in the Analyst page
    if (state.activePage === "analyst") {
      const loc = $("analyst-location").value;
      if (loc === "all" || msg.location === loc) prependAnalystLabel(msg);
    }
  }
}

// ── System status badge ───────────────────────────────────────────────────────
function updateSystemStatusBadge() {
  const faulted = Object.values(state.locationStates)
    .filter(s => s.active_fault).length;
  const badge = $("system-status");
  if (faulted > 0) {
    badge.textContent = `⚠ FAULT (${faulted})`;
    badge.className = "status-badge alert";
  } else {
    badge.textContent = "● NOMINAL";
    badge.className = "status-badge";
  }
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function apiDelete(path) {
  return apiFetch(path, { method: "DELETE" });
}

async function apiPost(path, body) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function refreshLocationStates() {
  try {
    const data = await apiFetch("/api/locations");
    state.locationStates = data;
    updateSystemStatusBadge();
    updateTwinIndicators();
    updateLocationList();
  } catch (_) {}
}

// ============================================================
//  DIGITAL TWIN  (Three.js)
// ============================================================
function buildDigitalTwin() {
  const container = $("twin-container");

  // Wait for twin.js module to register window.twinInit
  function tryInit() {
    if (typeof window.twinInit === "function") {
      window.twinInit(container, loc => {
        $("detail-location").value = loc;
        onDetailLocationChange();
        document.querySelector('.tab[data-page="detail"]').click();
      });
      // Push current state immediately if already loaded
      if (Object.keys(state.locationStates).length) {
        window.twinUpdate(state.locationStates);
      }
    } else {
      setTimeout(tryInit, 80);
    }
  }
  tryInit();

  $("btn-inject").addEventListener("click", injectFault);
  $("btn-reset-camera").addEventListener("click", () => {
    if (typeof window.twinResetCamera === "function") {
      window.twinResetCamera();
    }
  });
  $("btn-clear-faults").addEventListener("click", clearAllFaults);
  $("btn-clear-data").addEventListener("click", clearData);
  $("latch-popup-dismiss").addEventListener("click", dismissLatchPopup);
}

function updateTwinIndicators() {
  if (typeof window.twinUpdate === "function") {
    window.twinUpdate(state.locationStates);
  }
}

function updateLocationList() {
  const list = $("location-list");
  list.innerHTML = "";
  state.config.locations.forEach(loc => {
    const s = state.locationStates[loc] || {};
    const item = document.createElement("div");
    item.className = "location-item";
    item.innerHTML = `
      <div class="loc-dot ${s.active_fault ? "anomalous" : "nominal"}"></div>
      <span class="loc-name">${loc}</span>
      ${s.active_fault ? `<span class="loc-fault">${s.active_fault}</span>` : ""}
    `;
    item.addEventListener("click", () => {
      $("detail-location").value = loc;
      onDetailLocationChange();
      document.querySelector('.tab[data-page="detail"]').click();
    });
    list.appendChild(item);
  });
}

async function injectFault() {
  const location = $("inject-location").value;
  const fault = $("inject-fault").value;
  try {
    await apiPost("/api/faults/inject", { location, fault });
    showToast(`Fault injected: ${fault} @ ${location}`, "info");
    await refreshLocationStates();
  } catch (e) {
    showToast(`Error: ${e.message}`, "error");
  }
}

async function clearAllFaults() {
  try {
    await apiDelete("/api/faults");
    showToast("All faults cleared", "success");
    await refreshLocationStates();
  } catch (e) {
    showToast(`Error: ${e.message}`, "error");
  }
}

async function clearData() {
  if (!confirm("Clear ALL sensor data? This cannot be undone.")) return;
  try {
    await apiDelete("/api/data");

    // Refresh UI to reflect cleared state
    await refreshLocationStates();  
    await refreshDashboard();       
    refreshAlerts();        
    refreshAlertBadge();         

    showToast("Sensor data cleared", "success");
  } catch (e) {
    showToast(`Error: ${e.message}`, "error");
  }
}

// ============================================================
//  DASHBOARD — All-location cards
// ============================================================
const DASHBOARD_KEY_SENSORS = [
  "O2 partial pressure",
  "CO2 partial pressure",
  "Cabin pressure",
  "Temperature",
  "Humidity",
  "NH3",
  "CO",
];

const SENSOR_SHORT_NAMES = {
  "O2 partial pressure":  "O2 pp",
  "CO2 partial pressure": "CO2 pp",
  "Cabin pressure":       "Cabin P",
  "Temperature":          "Temp",
  "Humidity":             "Humidity",
  "NH3":                  "NH3",
  "CO":                   "CO",
};

async function buildDashboard() {
  await refreshDashboard();
}

async function refreshDashboard() {
  const grid = $("location-cards-grid");
  if (!grid) return;
  const { locations } = state.config;

  // Fetch any locations that have no cached sensor data yet
  const missing = locations.filter(loc => !state.locationData[loc]);
  if (missing.length) {
    const results = await Promise.allSettled(
      missing.map(loc => apiFetch(`/api/location/${encodeURIComponent(loc)}/latest`))
    );
    results.forEach((r, i) => {
      if (r.status === "fulfilled" && r.value?.data) {
        state.locationData[missing[i]] = r.value.data;
      }
    });
  }

  grid.innerHTML = "";
  locations.forEach(loc => grid.appendChild(makeLocationCard(loc)));
}

// Classify a raw sensor value against its nominal range and return bar/value CSS classes.
// Three zones: nominal (inside range), warning (outside range but within 10% margin),
// critical (more than 10% of span outside range). Used by both makeLocationCard and
// updateLocationCardInPlace to keep styling logic in one place.
function _sensorBarInfo(raw, range) {
  if (raw === undefined || range === undefined) {
    return { pct: 0, barCls: "bar-empty", valCls: "muted" };
  }
  const [lo, hi] = range;
  const span = hi - lo || 1;
  const pct  = Math.min(100, Math.max(0, (raw - lo) / span * 100));
  const margin = span * 0.1;
  if (raw < lo - margin || raw > hi + margin) return { pct, barCls: "bar-critical", valCls: "critical" };
  if (raw < lo            || raw > hi)        return { pct, barCls: "bar-warning",  valCls: "warning"  };
  return { pct, barCls: "bar-nominal", valCls: "nominal" };
}

function makeLocationCard(loc) {
  const locState  = state.locationStates[loc] || {};
  const sensorData = state.locationData[loc]  || {};
  const dqnRec    = state.dqnRecs[loc];
  const { parameter_nominal_ranges, parameter_units } = state.config;

  const activeFault = locState.active_fault;
  const isAnomalous = !!activeFault;

  const sensorRowsHtml = DASHBOARD_KEY_SENSORS.map(param => {
    const raw   = sensorData[param];
    const range = parameter_nominal_ranges[param];
    const short = SENSOR_SHORT_NAMES[param] || param;
    const { pct, barCls, valCls } = _sensorBarInfo(raw, range);
    const display = raw !== undefined ? formatVal(raw, param) : "—";
    return `
      <div class="loc-sensor-row" data-param="${param}">
        <span class="loc-sensor-name" title="${param}">${short}</span>
        <div class="loc-sensor-bar-wrap">
          <div class="loc-sensor-bar ${barCls}" style="width:${pct}%"></div>
        </div>
        <span class="loc-sensor-val ${valCls}">${display}</span>
      </div>`;
  }).join("");

  let dqnHtml = '<span class="loc-dqn muted">DQN: —</span>';
  if (dqnRec) {
    const isNoop = dqnRec.action_index === 0;
    const conf   = (dqnRec.confidence * 100).toFixed(0);
    const cls    = isNoop ? "nominal" : dqnRec.confidence >= 0.7 ? "critical" : "warning";
    dqnHtml = `<span class="loc-dqn ${cls}">DQN: ${dqnRec.action} (${conf}%)</span>`;
  }

  const isLatched = !!(locState.latched);
  const faultHtml = activeFault
    ? `<div class="loc-card-fault">
         ⚠ Detected: ${activeFault}
         ${isLatched ? `<button class="resolve-latch-btn" data-location="${loc}" title="Mark as resolved">Resolve</button>` : ""}
       </div>`
    : "";

  const cardId = `loc-card-${loc.replace(/\s+/g, "-").replace(/[^a-zA-Z0-9-]/g, "")}`;

  const card = document.createElement("div");
  card.className = `loc-card ${isAnomalous ? "anomalous" : "nominal"}`;
  card.id = cardId;
  card.dataset.location = loc;
  card.innerHTML = `
    <div class="loc-card-header">
      <span class="loc-card-name">${loc}</span>
      <span class="loc-status-badge ${isAnomalous ? "anomalous" : "nominal"}">${isAnomalous ? "FAULT" : "NOMINAL"}</span>
    </div>
    ${faultHtml}
    <div class="loc-card-sensors">${sensorRowsHtml}</div>
    <div class="loc-card-footer">${dqnHtml}</div>
  `;

  card.addEventListener("click", (e) => {
    if (e.target.classList.contains("resolve-latch-btn")) return;
    $("detail-location").value = loc;
    onDetailLocationChange();
    document.querySelector('.tab[data-page="detail"]').click();
    loadDetailChart();
  });

  const resolveBtn = card.querySelector(".resolve-latch-btn");
  if (resolveBtn) {
    resolveBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const location = resolveBtn.dataset.location;
      try {
        await apiDelete(`/api/faults/latch/${encodeURIComponent(location)}`);
        showToast(`Fault resolved @ ${location}`, "success");
        await refreshLocationStates();
      } catch (err) {
        showToast(`Error: ${err.message}`, "error");
      }
    });
  }

  return card;
}

function updateLocationCardInPlace(loc, sensorData, dqnRec, ifLabel) {
  const cardId = `loc-card-${loc.replace(/\s+/g, "-").replace(/[^a-zA-Z0-9-]/g, "")}`;
  const card = document.getElementById(cardId);
  if (!card) return;

  const locState   = state.locationStates[loc] || {};
  const activeFault = locState.active_fault || null;
  const isLatched   = !!(locState.latched);
  const isAnomalous = !!activeFault;
  const { parameter_nominal_ranges } = state.config;

  // Update card class and header badge
  card.className = `loc-card ${isAnomalous ? "anomalous" : "nominal"}`;
  const badge = card.querySelector(".loc-status-badge");
  if (badge) {
    badge.className = `loc-status-badge ${isAnomalous ? "anomalous" : "nominal"}`;
    badge.textContent = isAnomalous ? "FAULT" : "NOMINAL";
  }

  // Update fault banner + resolve button
  let faultEl = card.querySelector(".loc-card-fault");
  if (activeFault) {
    if (!faultEl) {
      faultEl = document.createElement("div");
      faultEl.className = "loc-card-fault";
      card.querySelector(".loc-card-header").insertAdjacentElement("afterend", faultEl);
    }
    let resolveBtn = faultEl.querySelector(".resolve-latch-btn");
    faultEl.innerHTML = `⚠ Detected: ${activeFault} ${isLatched ? `<button class="resolve-latch-btn" data-location="${loc}" title="Mark as resolved">Resolve</button>` : ""}`;
    resolveBtn = faultEl.querySelector(".resolve-latch-btn");
    if (resolveBtn) {
      resolveBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await apiDelete(`/api/faults/latch/${encodeURIComponent(loc)}`);
          showToast(`Fault resolved @ ${loc}`, "success");
          await refreshLocationStates();
        } catch (err) {
          showToast(`Error: ${err.message}`, "error");
        }
      });
    }
  } else if (faultEl) {
    faultEl.remove();
  }

  // Update sensor rows in place
  if (sensorData) {
    card.querySelectorAll(".loc-sensor-row[data-param]").forEach(row => {
      const param = row.dataset.param;
      const raw   = sensorData[param];
      const range = parameter_nominal_ranges[param];
      const { pct, barCls, valCls } = _sensorBarInfo(raw, range);

      const bar = row.querySelector(".loc-sensor-bar");
      if (bar) { bar.className = `loc-sensor-bar ${barCls}`; bar.style.width = `${pct}%`; }

      const val = row.querySelector(".loc-sensor-val");
      if (val) {
        val.className = `loc-sensor-val ${valCls}`;
        val.textContent = raw !== undefined ? formatVal(raw, param) : "—";
      }
    });
  }

  // Update DQN footer
  if (dqnRec) {
    const dqnEl = card.querySelector(".loc-dqn");
    if (dqnEl) {
      const isNoop = dqnRec.action_index === 0;
      const conf   = (dqnRec.confidence * 100).toFixed(0);
      const cls    = isNoop ? "nominal" : dqnRec.confidence >= 0.7 ? "critical" : "warning";
      dqnEl.className = `loc-dqn ${cls}`;
      dqnEl.textContent = `DQN: ${dqnRec.action} (${conf}%)`;
    }
  }
}

// ============================================================
//  DETAIL PAGE
// ============================================================
function onDetailLocationChange() {
  const loc = $("detail-location").value;
  const paramSel = $("detail-parameter");
  const allParams = Object.values(state.config.subsystem_parameters).flat();
  paramSel.innerHTML = "";
  allParams.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    paramSel.appendChild(opt);
  });
}

function buildDetail() {
  $("btn-detail-load").addEventListener("click", loadDetailChart);
  $("detail-live").addEventListener("change", e => {
    state.detailLive = e.target.checked;
  });

  // Register custom interaction mode for chart hover tolerance
  const Interaction = Chart.Interaction;
  Interaction.modes.customTolerance = function(chart, e, options, useFinalPosition) {
    const position = Chart.helpers.getRelativePosition(e, chart);
    const items = [];
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chart.getDatasetMeta(datasetIndex);
      if (meta.data) {
        meta.data.forEach((element, index) => {
          const dist = Math.hypot(
            position.x - element.x,
            position.y - element.y
          );
          if (dist <= options.radius) {
            items.push({ element, datasetIndex, index });
          }
        });
      }
    });
    return items;
  };
}

async function loadDetailChart() {
  const loc   = $("detail-location").value;
  const param = $("detail-parameter").value;
  const n     = parseInt($("detail-n").value, 10);

  let history;
  try {
    history = await apiFetch(
      `/api/location/${encodeURIComponent(loc)}/history/${encodeURIComponent(param)}?n=${n}`
    );
  } catch (e) {
    showToast(`Error loading history: ${e.message}`, "error");
    return;
  }

  const labels = history.map(r => r.timestamp.split("T")[1].split(".")[0]);
  const values = history.map(r => r.value);

  $("detail-table-param-header").textContent = param;

  // Nominal range bands
  const range = state.config.parameter_nominal_ranges[param];
  const unit  = state.config.parameter_units[param] || "";

  const ctx = $("detail-chart").getContext("2d");

  if (state.detailChart) state.detailChart.destroy();

  state.detailChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `${param} (${unit})`,
          data: values,
          borderColor: "#00e676",
          backgroundColor: "rgba(0,230,118,.08)",
          borderWidth: 1.5,
          pointRadius: 2,
          pointHoverRadius: 4,
          tension: 0.2,
        },
        range && {
          label: "Nominal Max",
          data: Array(values.length).fill(range[1]),
          borderColor: "rgba(255,171,0,.4)",
          borderDash: [4, 4],
          borderWidth: 1,
          pointRadius: 0,
          fill: false,
        },
        range && {
          label: "Nominal Min",
          data: Array(values.length).fill(range[0]),
          borderColor: "rgba(255,171,0,.4)",
          borderDash: [4, 4],
          borderWidth: 1,
          pointRadius: 0,
          fill: "1",
          backgroundColor: "rgba(255,171,0,.04)",
        },
      ].filter(Boolean),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {
        mode: "customTolerance",
        radius: 5,
      },
      plugins: {
        legend: {
          labels: { color: "#8499ac", font: { size: 10 } },
        },
      },

      scales: {
        x: {
          ticks: { color: "#546478", maxTicksLimit: 10, font: { size: 10 } },
          grid: { color: "rgba(30,45,66,.8)" },
        },
        y: {
          ticks: { color: "#546478", font: { size: 10 } },
          grid: { color: "rgba(30,45,66,.8)" },
        },
      },
    }
  });

  // Populate table
  const tbody = $("detail-table-body");
  tbody.innerHTML = "";
  history.forEach(r => {
    const tr = document.createElement("tr");
    const isOut = range && (r.value < range[0] || r.value > range[1]);
    tr.innerHTML = `
      <td>${r.timestamp}</td>
      <td class="${isOut ? "anomalous" : ""}">${formatVal(r.value, param)}</td>
    `;
    tbody.insertBefore(tr, tbody.firstChild);
  });
}

// Append one live point to the running detail chart and table.
// Keeps the window size bounded at `detail-n` points by shifting off the oldest.
// chart.update("none") skips animation so fast ticks don't cause visual jank.
// The nominal band datasets (datasets[1] and [2]) are flat lines — they repeat
// the same value so their length stays in sync with the data dataset.
function pushDetailPoint(timestamp, value, isAnomalous) {
  if (!state.detailChart) return;
  const chart = state.detailChart;
  const label = timestamp.split("T")[1].split(".")[0];
  const maxPoints = parseInt($("detail-n").value, 10);

  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);

  // Extend nominal band datasets by repeating their constant value
  chart.data.datasets.slice(1).forEach(ds => {
    if (ds && ds.data.length > 0) ds.data.push(ds.data[0]);
  });

  // Slide window: drop oldest point when over limit
  if (chart.data.labels.length > maxPoints) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => ds.data.shift());
  }

  chart.update("none");   // skip animation for smooth live streaming

  // Table row
  const tbody = $("detail-table-body");
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td>${timestamp}</td>
    <td class="${isAnomalous ? "anomalous" : ""}">${formatVal(value, $("detail-parameter").value)}</td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  if (tbody.rows.length > maxPoints) tbody.deleteRow(tbody.rows.length - 1);
}

// ============================================================
//  AI ANALYST — chat interface
// ============================================================

let _chatHistory   = [];   // [{role, content}]
let _chatStreaming  = false;

function buildAnalyst() {
  // Prepend "All Locations" option to the location select
  const locSel = $("analyst-location");
  const allOpt = document.createElement("option");
  allOpt.value = "all";
  allOpt.textContent = "All Locations";
  locSel.insertBefore(allOpt, locSel.firstChild);
  locSel.value = "all";

  locSel.addEventListener("change", refreshAnalystLabels);

  $("btn-analyst-clear").addEventListener("click", _clearChat);

  const input  = $("chat-input");
  const sendBtn = $("btn-chat-send");

  sendBtn.addEventListener("click", _sendChatMessage);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      _sendChatMessage();
    }
  });
  input.addEventListener("input", _resizeChatInput);

  // Fetch backend status once on page load
  _refreshBackendBadge();
}

// ── Backend status badge ─────────────────────────────────────────────────────
async function _refreshBackendBadge() {
  try {
    const s = await apiFetch("/api/ai/status");
    _updateBackendBadge(s.backend);
  } catch (_) {
    _updateBackendBadge("none");
  }
}

function _updateBackendBadge(backend) {
  const dot   = $("analyst-backend-dot");
  const label = $("analyst-backend-label");
  dot.className   = "backend-dot backend-dot--" + backend;
  label.textContent = backend === "ollama" ? "Ollama (local)"
                    : backend === "groq"   ? "Groq (fallback)"
                    :                        "No backend";
}

// ── Chat helpers ─────────────────────────────────────────────────────────────
function _clearChat() {
  _chatHistory = [];
  const msgs = $("chat-messages");
  msgs.innerHTML = "";
  const welcome = document.createElement("div");
  welcome.className = "chat-welcome";
  welcome.id = "chat-welcome";
  welcome.innerHTML = `
    <div class="chat-welcome-icon">◈</div>
    <div class="chat-welcome-title">AURA AI Analyst</div>
    <div class="chat-welcome-sub">
      Ask anything about ISS ECLSS sensor data, anomalies, fault analysis, or maintenance
      status. The AI has read-only access to all live system data across all modules.
    </div>`;
  msgs.appendChild(welcome);
}

function _resizeChatInput() {
  const el = $("chat-input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

function _appendMessage(role, content) {
  const welcome = $("chat-welcome");
  if (welcome) welcome.remove();

  const msgs = $("chat-messages");
  const wrap = document.createElement("div");
  wrap.className = `chat-message chat-message--${role}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  if (role === "ai") {
    const avatar = document.createElement("div");
    avatar.className = "chat-avatar";
    avatar.textContent = "◈";
    wrap.appendChild(avatar);
  }

  const contentEl = document.createElement("div");
  contentEl.className = "chat-bubble-content";
  if (content) contentEl.innerHTML = role === "ai" ? _renderMarkdown(content) : _escHtml(content);
  bubble.appendChild(contentEl);
  wrap.appendChild(bubble);
  msgs.appendChild(wrap);
  msgs.scrollTop = msgs.scrollHeight;
  return wrap;   // caller can grab contentEl later
}

// ── Send + stream ────────────────────────────────────────────────────────────
async function _sendChatMessage() {
  const input = $("chat-input");
  const text  = input.value.trim();
  if (!text || _chatStreaming) return;

  input.value = "";
  _resizeChatInput();

  _chatHistory.push({ role: "user", content: text });
  _appendMessage("user", text);

  // AI bubble placeholder
  const aiWrap    = _appendMessage("ai", "");
  const contentEl = aiWrap.querySelector(".chat-bubble-content");
  contentEl.innerHTML = '<span class="chat-typing"><span></span><span></span><span></span></span>';

  _chatStreaming = true;
  $("btn-chat-send").disabled = true;

  const model = $("analyst-model").value;
  let aiText  = "";
  let gotFirstToken = false;

  try {
    const response = await fetch("/api/ai/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ messages: _chatHistory, model }),
    });

    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();   // keep any incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch (_) { continue; }

        if (evt.backend) {
          _updateBackendBadge(evt.backend);
        }
        if (evt.token) {
          if (!gotFirstToken) {
            contentEl.innerHTML = "";
            gotFirstToken = true;
          }
          aiText += evt.token;
          contentEl.innerHTML = _renderMarkdown(aiText);
          $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
        }
        if (evt.error) {
          contentEl.innerHTML = `<span class="chat-error">Error: ${_escHtml(evt.error)}</span>`;
        }
      }
    }

    if (aiText) {
      _chatHistory.push({ role: "assistant", content: aiText });
    }
  } catch (e) {
    contentEl.innerHTML = `<span class="chat-error">Error: ${_escHtml(e.message)}</span>`;
  } finally {
    _chatStreaming = false;
    $("btn-chat-send").disabled = false;
    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
  }
}

// ── Markdown renderer ────────────────────────────────────────────────────────
// Lightweight subset renderer — handles fenced code blocks, inline code,
// bold/italic, headings (h1-h3), unordered and ordered lists, and paragraphs.
// Does NOT use a full Markdown library to keep the bundle zero-dependency.
function _escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _renderMarkdown(text) {
  // Split on fenced code blocks first so inline Markdown patterns don't corrupt code.
  const parts  = [];
  const codeRe = /```([\w]*)\n?([\s\S]*?)(?:```|$)/g;
  let last = 0, m;
  while ((m = codeRe.exec(text)) !== null) {
    if (m.index > last) parts.push({ t: "text", s: text.slice(last, m.index) });
    parts.push({ t: "code", s: m[2] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ t: "text", s: text.slice(last) });

  return parts.map(p => {
    if (p.t === "code") {
      return `<pre class="chat-code-block"><code>${_escHtml(p.s.trimEnd())}</code></pre>`;
    }
    let h = _escHtml(p.s);
    // Inline code
    h = h.replace(/`([^`\n]+)`/g, '<code class="chat-inline-code">$1</code>');
    // Bold / italic
    h = h.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/\*([^*\n]+)\*/g,     "<em>$1</em>");
    // Headings (### ## #)
    h = h.replace(/^### (.+)$/gm, '<div class="chat-h3">$1</div>');
    h = h.replace(/^## (.+)$/gm,  '<div class="chat-h2">$1</div>');
    h = h.replace(/^# (.+)$/gm,   '<div class="chat-h1">$1</div>');
    // Unordered lists — group consecutive items into <ul>
    h = h.replace(/((?:^[-*•] .+(?:\n|$))+)/gm, match => {
      const items = match.trimEnd().split("\n")
        .map(l => `<li>${l.replace(/^[-*•] /, "")}</li>`).join("");
      return `<ul>${items}</ul>`;
    });
    // Ordered lists
    h = h.replace(/((?:^\d+\. .+(?:\n|$))+)/gm, match => {
      const items = match.trimEnd().split("\n")
        .map(l => `<li>${l.replace(/^\d+\. /, "")}</li>`).join("");
      return `<ol>${items}</ol>`;
    });
    // Paragraphs
    h = h.replace(/\n\n+/g, "</p><p>").replace(/\n/g, "<br>");
    return `<p>${h}</p>`;
  }).join("");
}

// ── Live anomaly sidebar ─────────────────────────────────────────────────────
async function refreshAnalystLabels() {
  const loc  = $("analyst-location").value;
  const list = $("analyst-labels-list");
  list.innerHTML = '<div class="loading">Loading…</div>';

  try {
    // If "all" scope, pull from the first location that has data as a sample;
    // the live WS feed will fill in the rest in real time.
    const target = loc === "all" ? state.config.locations[0] : loc;
    const readings = await apiFetch(
      `/api/location/${encodeURIComponent(target)}/readings?n=15`
    );
    list.innerHTML = "";
    if (!readings.length) {
      list.innerHTML = '<div class="loading">No data yet</div>';
      return;
    }
    readings.reverse().forEach(r => prependAnalystLabelFromReading(r));
  } catch (e) {
    list.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

function prependAnalystLabelFromReading(r) {
  const list   = $("analyst-labels-list");
  const isAnom = r.if_label === -1;
  const ts     = r.timestamp ? r.timestamp.split("T")[1].split(".")[0] : "—";

  let rfText = "";
  if (r.rf_classification) {
    const top = Object.entries(r.rf_classification).sort((a, b) => b[1] - a[1])[0];
    if (top) rfText = `${top[0]} (${(top[1] * 100).toFixed(0)}%)`;
  }

  const item = document.createElement("div");
  item.className = `label-item ${isAnom ? "anomalous" : ""}`;
  item.innerHTML = `
    <div class="label-ts">${ts}</div>
    <div class="label-if ${isAnom ? "anomalous" : "nominal"}">${isAnom ? "ANOMALOUS" : "NOMINAL"}</div>
    ${rfText ? `<div class="label-rf">${rfText}</div>` : ""}
  `;
  list.insertBefore(item, list.firstChild);
  if (list.children.length > 15) list.removeChild(list.lastChild);
}

function prependAnalystLabel(msg) {
  prependAnalystLabelFromReading({
    if_label:          msg.if_label,
    rf_classification: msg.rf_classification,
    timestamp:         msg.timestamp,
  });
}

// ============================================================
//  TRENDS PAGE
// ============================================================
// ── Trends auto-refresh timer ─────────────────────────────────────────────────
// Managed entirely by the auto-refresh checkbox. Only one timer runs at a time.
// Without this discipline, enabling the checkbox while the page timer was already
// running would create a second 30-second interval causing double refreshes.
let _trendsAutoTimer = null;

function buildTrends() {
  $("btn-trends-load").addEventListener("click", refreshTrends);

  // Refresh immediately when location changes (only if auto-refresh is on)
  $("trends-location").addEventListener("change", () => {
    if ($("trends-auto").checked) refreshTrends();
  });

  // Toggle the single shared timer on checkbox change
  $("trends-auto").addEventListener("change", e => {
    clearInterval(_trendsAutoTimer);   // always clear first to avoid duplicates
    if (e.target.checked) {
      _trendsAutoTimer = setInterval(refreshTrends, 30000);
    }
  });
}

async function refreshTrends() {
  const location = $("trends-location").value;
  const n = $("trends-n").value;
  const grid = $("trends-grid");
  grid.innerHTML = '<div class="loading">Analyzing trends…</div>';

  try {
    const res = await apiFetch(
      `/api/location/${encodeURIComponent(location)}/trends?n=${n}`
    );
    grid.innerHTML = "";
    if (!res.trends || res.trends.length === 0) {
      grid.innerHTML = '<div class="loading">No data yet — wait for readings to accumulate.</div>';
      return;
    }

    res.trends.forEach(t => {
      if (t.status === "insufficient_data") return;
      const card = document.createElement("div");
      card.className = `trend-card ${t.severity}`;

      const mk = t.mann_kendall || {};
      const trendClass = mk.trend === "increasing" ? "increasing"
                       : mk.trend === "decreasing" ? "decreasing"
                       : "no-trend";
      const trendLabel = mk.trend === "no trend" ? "stable" : mk.trend;

      const inNominal = t.nominal_range
        ? (t.current_value >= t.nominal_range[0] && t.current_value <= t.nominal_range[1])
        : true;
      const valueClass = inNominal ? "" : (t.severity === "critical" ? "style=\"color:var(--danger)\"" : "style=\"color:var(--warning)\"");

      card.innerHTML = `
        <div class="trend-header">
          <span class="trend-param">${t.param}</span>
          <span class="trend-severity ${t.severity}">${t.severity.toUpperCase()}</span>
        </div>
        <div class="trend-value" ${valueClass}>${formatVal(t.current_value, t.param)}<span>${t.unit}</span></div>
        ${t.nominal_range ? `<div class="trend-stat" style="margin-bottom:4px">Nominal: ${t.nominal_range[0]} – ${t.nominal_range[1]} ${t.unit}</div>` : ""}
        <div class="trend-stats">
          ${mk.significant !== undefined ? `<span class="trend-stat">τ=<strong>${mk.tau}</strong></span>` : ""}
          ${mk.p_value !== undefined ? `<span class="trend-stat">p=<strong>${mk.p_value}</strong></span>` : ""}
          ${t.z_score !== undefined ? `<span class="trend-stat">z=<strong>${t.z_score}</strong></span>` : ""}
          ${t.sens_slope_per_reading !== undefined ? `<span class="trend-stat">slope=<strong>${(t.sens_slope_per_reading * 3600).toFixed(4)}/hr</strong></span>` : ""}
        </div>
        <span class="trend-trend ${trendClass}">${trendLabel}${mk.significant ? " ✓" : ""}</span>
        ${t.recommendation ? `<div class="trend-rec">${t.recommendation}</div>` : ""}
      `;

      card.addEventListener("click", () => {
        $("detail-location").value = location;
        $("detail-parameter").value = t.param;
        document.querySelector('.tab[data-page="detail"]').click();
        loadDetailChart();
      });

      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

// ============================================================
//  ALERTS PAGE
// ============================================================
function buildAlerts() {
  const { locations } = state.config;

  // Populate location filter
  const sel = $("alerts-location-filter");
  sel.innerHTML = '<option value="">All locations</option>';
  locations.forEach(loc => {
    const opt = document.createElement("option");
    opt.value = loc;
    opt.textContent = loc;
    sel.appendChild(opt);
  });

  $("btn-alerts-refresh").addEventListener("click", refreshAlerts);
  $("btn-ack-all").addEventListener("click", async () => {
    await apiPost("/api/alerts/acknowledge-all", {});
    refreshAlerts();
    refreshAlertBadge();
  });
  $("alerts-unacked-only").addEventListener("change", refreshAlerts);
  $("alerts-location-filter").addEventListener("change", refreshAlerts);
}

async function refreshAlerts() {
  const location = $("alerts-location-filter").value;
  const unacked  = $("alerts-unacked-only").checked;
  const list = $("alerts-list");
  list.innerHTML = '<div class="loading">Loading…</div>';

  try {
    let url = `/api/alerts?limit=100&unacked_only=${unacked}`;
    if (location) url += `&location=${encodeURIComponent(location)}`;
    const alerts = await apiFetch(url);

    list.innerHTML = "";
    if (!alerts.length) {
      list.innerHTML = '<div class="loading">No alerts found.</div>';
      return;
    }

    alerts.forEach(a => {
      const item = document.createElement("div");
      item.className = `alert-item ${a.severity.toLowerCase()} ${a.acknowledged ? "acked" : ""}`;

      const topProb = a.top_probability ? ` (${(a.top_probability * 100).toFixed(0)}%)` : "";

      item.innerHTML = `
        <span class="alert-severity ${a.severity.toLowerCase()}">${a.severity}</span>
        <div class="alert-body">
          <span class="alert-title">
            ${a.fault_type ? `${a.fault_type}${topProb}` : "Anomaly detected"}
            — ${a.location}
          </span>
          <span class="alert-meta">${a.timestamp}</span>
        </div>
        <button class="btn btn-ghost btn-sm" data-id="${a.id}"
                ${a.acknowledged ? "disabled" : ""}>
          ${a.acknowledged ? "Acked" : "Ack"}
        </button>
      `;

      item.querySelector("button").addEventListener("click", async e => {
        const id = parseInt(e.target.dataset.id);
        await apiPost(`/api/alerts/${id}/acknowledge`, {});
        refreshAlerts();
        refreshAlertBadge();
      });

      list.appendChild(item);
    });

    refreshAlertBadge();
  } catch (e) {
    list.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

// ============================================================
//  MAINTENANCE PAGE
// ============================================================
function buildMaintenance() {
  $("btn-maintenance-refresh").addEventListener("click", refreshMaintenance);
}

async function refreshMaintenance() {
  const replGrid = $("replacement-grid");
  const calBody  = $("calibration-body");
  replGrid.innerHTML = '<div class="loading">Loading…</div>';
  calBody.innerHTML  = '';

  let data;
  try {
    data = await apiFetch("/api/maintenance");
  } catch (e) {
    replGrid.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
    return;
  }

  // Mission elapsed clock
  const h = data.mission_elapsed_hours;
  const d = data.mission_elapsed_days;
  $("mission-elapsed").textContent = h < 48
    ? `${h.toFixed(1)} h`
    : `${d.toFixed(1)} d  (${h.toFixed(0)} h)`;

  // ── Replacement cards ──────────────────────────────────────────────────
  replGrid.innerHTML = "";
  data.replacement_schedule.forEach(item => {
    const pct     = item.pct_life_used;
    const barCls  = pct >= 90 ? "bar-critical" : pct >= 75 ? "bar-warning" : pct >= 50 ? "bar-caution" : "bar-nominal";
    const statCls = item.status.toLowerCase();

    const card = document.createElement("div");
    card.className = `repl-card ${statCls}`;
    const maintTypeBadge = item.maintenance_type === "calendar_based"
      ? '<span class="maint-type-badge calendar">CALENDAR</span>'
      : '<span class="maint-type-badge condition">CONDITION</span>';
    const smacHtml = item.smac_trigger
      ? `<div class="repl-smac"><span class="smac-label">TRIGGER:</span> ${item.smac_trigger}</div>`
      : '';
    const sourceHtml = item.source
      ? `<div class="repl-source">Source: ${item.source}</div>`
      : '';
    card.innerHTML = `
      <div class="repl-header">
        <span class="repl-name">${item.subsystem}</span>
        <div class="repl-badges">
          ${maintTypeBadge}
          <span class="maint-badge ${statCls}">${item.status}</span>
        </div>
      </div>
      <div class="repl-subsystem-full">${item.subsystem_full || ''}</div>
      <div class="repl-bar-wrap">
        <div class="repl-bar ${barCls}" style="width:${pct}%"></div>
      </div>
      <div class="repl-stats">
        <span>${pct}% life used</span>
        <span>${fmtHours(item.remaining_hours)} remaining</span>
      </div>
      <div class="repl-mtbf">MTBF ${fmtHours(item.mtbf_hours)} &middot; Elapsed ${fmtHours(item.elapsed_hours)}</div>
      <div class="repl-action"><span class="action-label">ACTION:</span> ${item.primary_action || ''}</div>
      <div class="repl-interval">${item.interval_note || ''}</div>
      ${smacHtml}
      ${sourceHtml}
    `;
    replGrid.appendChild(card);
  });

  // ── Calibration table ──────────────────────────────────────────────────
  data.calibration_schedule.forEach(item => {
    const statCls = item.status.toLowerCase().replace("_", "-");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="cal-param">${item.parameter} <span class="cal-unit">${item.unit}</span></td>
      <td>${item.drift_per_week_pct}%/week</td>
      <td>
        <div class="cal-bar-wrap">
          <div class="cal-bar ${item.cumulative_drift_pct >= 100 ? 'bar-critical' : item.cumulative_drift_pct >= 75 ? 'bar-warning' : 'bar-nominal'}"
               style="width:${Math.min(item.cumulative_drift_pct,100)}%"></div>
        </div>
        <span class="cal-pct">${item.cumulative_drift_pct}%</span>
      </td>
      <td>${item.weeks_until_cal <= 0 ? '<span style="color:var(--danger)">Overdue</span>' : item.weeks_until_cal + ' wk'}</td>
      <td><span class="maint-badge ${statCls}">${item.status.replace("_", " ")}</span></td>
    `;
    calBody.appendChild(tr);
  });
}

function fmtHours(h) {
  if (h >= 8760) return `${(h / 8760).toFixed(1)} yr`;
  if (h >= 168)  return `${(h / 168).toFixed(1)} wk`;
  if (h >= 24)   return `${(h / 24).toFixed(1)} d`;
  return `${h.toFixed(0)} h`;
}

// ============================================================
//  UTILITIES
// ============================================================
function formatVal(v, param) {
  if (v === null || v === undefined) return "—";
  const n = parseFloat(v);
  if (isNaN(n)) return String(v);
  return n.toFixed(3);
}

let toastTimeout = null;
function showToast(msg, type = "info") {
  const el = $("toast");
  el.textContent = msg;
  el.className = `toast ${type}`;
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => el.classList.add("hidden"), 4000);
}

// ============================================================
//  LATCH ALERT POPUP & ALARM
// ============================================================
// Shown when the backend broadcasts a "latched" alert (RF confidence >= 90%).
// The popup blocks the UI until dismissed; the optional Web Audio klaxon provides
// an audible warning. Set ALARM_SOUND_ENABLED = true to activate the sound.
const ALARM_SOUND_ENABLED = false;

let _alarmCtx = null;
let _alarmNodes = [];

function _startAlarm() {
  if (!ALARM_SOUND_ENABLED) return;
  _stopAlarm();
  _alarmCtx = new (window.AudioContext || window.webkitAudioContext)();

  function _beepCycle() {
    if (!_alarmCtx) return;
    const now = _alarmCtx.currentTime;
    // Two-tone klaxon: 880 Hz then 660 Hz, each 0.18 s, gap 0.12 s
    [[880, now], [660, now + 0.3]].forEach(([freq, t]) => {
      const osc  = _alarmCtx.createOscillator();
      const gain = _alarmCtx.createGain();
      osc.type = "square";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.18, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
      osc.connect(gain);
      gain.connect(_alarmCtx.destination);
      osc.start(t);
      osc.stop(t + 0.18);
      _alarmNodes.push(osc, gain);
    });
  }

  _beepCycle();
  _alarmCtx._interval = setInterval(_beepCycle, 1200);
}

function _stopAlarm() {
  if (_alarmCtx) {
    clearInterval(_alarmCtx._interval);
    _alarmNodes.forEach(n => { try { n.disconnect(); } catch (_) {} });
    _alarmNodes = [];
    _alarmCtx.close();
    _alarmCtx = null;
  }
}

function showLatchPopup(location, faultType, confidence) {
  $("latch-popup-location").textContent = location;
  $("latch-popup-fault").textContent    = faultType;
  $("latch-popup-conf").textContent     = `RF Confidence: ${(confidence * 100).toFixed(0)}%`;
  $("latch-overlay").classList.remove("hidden");
  _startAlarm();
}

function dismissLatchPopup() {
  $("latch-overlay").classList.add("hidden");
  _stopAlarm();
}

// ── Start ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", boot);
