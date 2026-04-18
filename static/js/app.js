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
  lstmRecs: {},                // {location: {failure_prob, rul_hours}} — from WS ticks
  locationData: {},            // {location: {param: value}} — latest sensor readings
  ws: null,                    // active WebSocket instance
  wsConnected: false,
  activePage: "twin",          // currently visible tab
  detailChart: null,           // Chart.js instance in the drilldown view (destroyed on each load)
  detailLive: true,            // whether live WS ticks push new points to the drilldown chart
  detailMiniCharts: {},        // {param: Chart instance} — sparklines in overview grid
  detailDrillParam: null,      // param currently open in drilldown (null = overview mode)
  detailPendingDrill: null,    // param to drill into after overview finishes loading
  detailAnomalyFlags: [],      // bool[] parallel to drilldown chart data — true = IF anomalous
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
  buildSettings();
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
      if (page === "detail")    loadDetailOverview();
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

  // Settings faults tab
  populateSelect("s-inject-location", locations);
  populateSelect("s-inject-fault", faults);
  _updateFaultDesc();

  // Dashboard
  $("btn-dashboard-refresh").addEventListener("click", refreshDashboard);

  // Detail
  populateSelect("detail-location", locations);
  $("detail-location").addEventListener("change", onDetailLocationChange);

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
  if (_wsKeepaliveTimer) clearInterval(_wsKeepaliveTimer);
  _wsKeepaliveTimer = setInterval(() => {
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
    if (msg.dqn)  state.dqnRecs[msg.location]  = msg.dqn;
    if (msg.lstm) state.lstmRecs[msg.location] = msg.lstm;
    // Merge fault/anomaly state so dashboard cards stay accurate without waiting for "state"
    if (!state.locationStates[msg.location]) state.locationStates[msg.location] = {};
    state.locationStates[msg.location].active_fault = msg.active_fault || null;
    state.locationStates[msg.location].is_anomalous = msg.if_label === -1;

    // Only update dashboard cards in-place when the dashboard is visible — avoids
    // touching the DOM for pages the user isn't looking at
    if (state.activePage === "dashboard") {
      updateLocationCardInPlace(msg.location, msg.data, msg.dqn, msg.lstm, msg.if_label);
    }
    // Update detail page live — mini-chart sparklines in overview, or drilldown chart
    if (state.activePage === "detail" && state.detailLive && msg.data) {
      const loc = $("detail-location").value;
      if (msg.location === loc) {
        // Update every sparkline that has data for this tick
        Object.entries(msg.data).forEach(([param, value]) => {
          const chart = state.detailMiniCharts[param];
          if (!chart) return;
          const label = msg.timestamp.split("T")[1].split(".")[0];
          chart.data.labels.push(label);
          chart.data.datasets[0].data.push(value);
          if (chart.data.labels.length > 50) {
            chart.data.labels.shift();
            chart.data.datasets[0].data.shift();
          }
          chart.update("none");
          // Refresh the value label on the card
          const safeId = `mini-chart-${param.replace(/[^a-zA-Z0-9]/g, "_")}`;
          const canvas = document.getElementById(safeId);
          if (canvas) {
            const valEl = canvas.closest(".detail-mini-card")?.querySelector(".detail-mini-val");
            if (valEl) {
              const range = state.config.parameter_nominal_ranges[param];
              const unit  = state.config.parameter_units[param] || "";
              const isOut = range && (value < range[0] || value > range[1]);
              valEl.textContent = formatVal(value, param) + "\u00a0" + unit;
              valEl.className = `detail-mini-val${isOut ? " anomalous" : ""}`;
            }
          }
        });
        // Also push to the drilldown chart if one is open
        const drillParam = state.detailDrillParam;
        if (drillParam && drillParam in msg.data) {
          pushDetailPoint(msg.timestamp, msg.data[drillParam], msg.if_label === -1);
        }
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

  $("btn-reset-camera").addEventListener("click", () => {
    if (typeof window.twinResetCamera === "function") {
      window.twinResetCamera();
    }
  });
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
      document.querySelector('.tab[data-page="detail"]').click();
    });
    list.appendChild(item);
  });
}

async function injectFault() {
  const location = $("s-inject-location").value;
  const fault = $("s-inject-fault").value;
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

function _lstmStatusInfo(lstm) {
  if (!lstm) return { label: "—", cls: "muted" };
  const p = lstm.failure_prob;
  if (p < 0.25) return { label: "NOMINAL", cls: "nominal" };
  if (p < 0.50) return { label: "WATCH",   cls: "warning" };
  if (p < 0.75) return { label: "CAUTION", cls: "warning" };
  return             { label: "CRITICAL", cls: "critical" };
}

function makeLocationCard(loc) {
  const locState  = state.locationStates[loc] || {};
  const sensorData = state.locationData[loc]  || {};
  const dqnRec    = state.dqnRecs[loc];
  const lstmRec   = state.lstmRecs[loc];
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
  const { label: lstmLabel, cls: lstmCls } = _lstmStatusInfo(lstmRec);
  const lstmHtml = `<span class="loc-lstm ${lstmCls}">LSTM: ${lstmLabel}</span>`;

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
    <div class="loc-card-footer">${dqnHtml}${lstmHtml}</div>
  `;

  card.addEventListener("click", (e) => {
    if (e.target.classList.contains("resolve-latch-btn")) return;
    $("detail-location").value = loc;
    document.querySelector('.tab[data-page="detail"]').click();
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

function updateLocationCardInPlace(loc, sensorData, dqnRec, lstmRec, ifLabel) {
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

  // Update LSTM status
  if (lstmRec) {
    const lstmEl = card.querySelector(".loc-lstm");
    if (lstmEl) {
      const { label, cls } = _lstmStatusInfo(lstmRec);
      lstmEl.className = `loc-lstm ${cls}`;
      lstmEl.textContent = `LSTM: ${label}`;
    }
  }
}

// ============================================================
//  DETAIL PAGE
// ============================================================
function onDetailLocationChange() {
  closeDetailDrilldown();
  loadDetailOverview();
}

function buildDetail() {
  $("btn-detail-back").addEventListener("click", closeDetailDrilldown);
  $("btn-detail-reload").addEventListener("click", loadDetailChart);
  $("detail-live").addEventListener("change", e => {
    state.detailLive = e.target.checked;
  });

  // Register custom interaction mode for drilldown chart hover tolerance
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

async function loadDetailOverview() {
  const loc = $("detail-location").value;
  if (!loc) return;

  const grid = $("detail-mini-grid");
  grid.innerHTML = '<div class="loading">Loading…</div>';

  // Destroy existing sparklines before rebuilding
  Object.values(state.detailMiniCharts).forEach(c => c.destroy());
  state.detailMiniCharts = {};

  const allParams = Object.values(state.config.subsystem_parameters).flat();

  const results = await Promise.allSettled(
    allParams.map(p =>
      apiFetch(`/api/location/${encodeURIComponent(loc)}/history?parameter=${encodeURIComponent(p)}&n=50`)
    )
  );

  grid.innerHTML = "";
  allParams.forEach((param, i) => {
    const history = results[i].status === "fulfilled" ? results[i].value : [];
    grid.appendChild(buildMiniCard(param, history));
  });

  // Status badge
  const locState = state.locationStates[loc] || {};
  const statusEl = $("detail-ov-status");
  if (locState.active_fault) {
    statusEl.textContent = `⚠ ${locState.active_fault}`;
    statusEl.className = "detail-ov-status fault";
  } else if (locState.is_anomalous) {
    statusEl.textContent = "ANOMALOUS";
    statusEl.className = "detail-ov-status anomalous";
  } else {
    statusEl.textContent = "NOMINAL";
    statusEl.className = "detail-ov-status nominal";
  }

  // Open drilldown if a caller queued one before overview was loaded
  if (state.detailPendingDrill) {
    const p = state.detailPendingDrill;
    state.detailPendingDrill = null;
    openDetailDrilldown(p);
  }
}

function buildMiniCard(param, history) {
  const range = state.config.parameter_nominal_ranges[param];
  const unit  = state.config.parameter_units[param] || "";
  const latestVal = history.length ? history[history.length - 1].value : null;
  const isOut = range && latestVal !== null && (latestVal < range[0] || latestVal > range[1]);

  const card = document.createElement("div");
  card.className = `detail-mini-card${isOut ? " out-of-range" : ""}`;
  const safeId = `mini-chart-${param.replace(/[^a-zA-Z0-9]/g, "_")}`;
  card.innerHTML = `
    <div class="detail-mini-header">
      <span class="detail-mini-name">${param}</span>
      <span class="detail-mini-val${isOut ? " anomalous" : ""}">${latestVal !== null ? formatVal(latestVal, param) + "\u00a0" + unit : "—"}</span>
    </div>
    <div class="detail-mini-chart-wrap"><canvas id="${safeId}"></canvas></div>
  `;
  card.addEventListener("click", () => openDetailDrilldown(param));

  // Build sparkline after card is in DOM (need canvas reachable)
  setTimeout(() => {
    const canvas = document.getElementById(safeId);
    if (!canvas) return;
    const labels = history.map(r => r.timestamp.split("T")[1].split(".")[0]);
    const values = history.map(r => r.value);
    const color  = isOut ? "#ff5252" : "#00e676";
    const chart  = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: isOut ? "rgba(255,82,82,.08)" : "rgba(0,230,118,.06)",
          borderWidth: 1,
          pointRadius: 0,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
    state.detailMiniCharts[param] = chart;
  }, 0);

  return card;
}

function openDetailDrilldown(param) {
  state.detailDrillParam = param;
  $("detail-parameter").value = param;
  const loc = $("detail-location").value;
  $("detail-dd-title").textContent = `${loc} — ${param}`;
  $("detail-overview").classList.add("hidden");
  $("detail-drilldown").classList.remove("hidden");
  loadDetailChart();
}

function closeDetailDrilldown() {
  state.detailDrillParam = null;
  state.detailAnomalyFlags = [];
  $("detail-drilldown").classList.add("hidden");
  $("detail-overview").classList.remove("hidden");
}

async function loadDetailChart() {
  const loc   = $("detail-location").value;
  const param = state.detailDrillParam;
  const n     = parseInt($("detail-n").value, 10);
  if (!param) return;

  let history;
  try {
    history = await apiFetch(
      `/api/location/${encodeURIComponent(loc)}/history?parameter=${encodeURIComponent(param)}&n=${n}`
    );
  } catch (e) {
    showToast(`Error loading history: ${e.message}`, "error");
    return;
  }

  const labels = history.map(r => r.timestamp.split("T")[1].split(".")[0]);
  const values = history.map(r => r.value);

  // Maintain a parallel bool array — callback reads it fresh each render, never gets out of sync
  state.detailAnomalyFlags = history.map(r => !!r.anomalous);

  $("detail-table-param-header").textContent = param;

  const range = state.config.parameter_nominal_ranges[param];
  const unit  = state.config.parameter_units[param] || "";

  const ptColor = ctx => state.detailAnomalyFlags[ctx.dataIndex] ? "#ff3d40" : "#00e676";

  const ctx2d = $("detail-chart").getContext("2d");
  if (state.detailChart) state.detailChart.destroy();

  state.detailChart = new Chart(ctx2d, {
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
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: ptColor,
          pointBorderColor: ptColor,
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
      interaction: { mode: "customTolerance", radius: 5 },
      plugins: {
        legend: { labels: { color: "#8499ac", font: { size: 10 } } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const r = history[ctx.dataIndex];
              const flag = r && r.anomalous ? " ⚠ ANOMALOUS" : "";
              return ` ${formatVal(ctx.parsed.y, param)} ${unit}${flag}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: "#546478", maxTicksLimit: 12, font: { size: 10 } },
          grid: { color: "rgba(30,45,66,.8)" },
        },
        y: {
          ticks: { color: "#546478", font: { size: 10 } },
          grid: { color: "rgba(30,45,66,.8)" },
        },
      },
    },
  });

  // Populate table — newest first, anomalous rows highlighted
  const tbody = $("detail-table-body");
  tbody.innerHTML = "";
  for (let i = history.length - 1; i >= 0; i--) {
    const r  = history[i];
    const tr = document.createElement("tr");
    if (r.anomalous) tr.classList.add("anomalous-row");
    tr.innerHTML = `
      <td>${r.timestamp}</td>
      <td class="${r.anomalous ? "anomalous" : ""}">${formatVal(r.value, param)}${r.anomalous ? ' <span class="anom-badge">⚠</span>' : ""}</td>
    `;
    tbody.appendChild(tr);
  }
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
  state.detailAnomalyFlags.push(isAnomalous);

  // Extend nominal band datasets by repeating their constant value
  chart.data.datasets.slice(1).forEach(ds => {
    if (ds && ds.data.length > 0) ds.data.push(ds.data[0]);
  });

  // Slide window: drop oldest point when over limit
  if (chart.data.labels.length > maxPoints) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => ds.data.shift());
    state.detailAnomalyFlags.shift();
  }

  chart.update("none");   // skip animation for smooth live streaming

  // Table row
  const tbody = $("detail-table-body");
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td>${timestamp}</td>
    <td class="${isAnomalous ? "anomalous" : ""}">${formatVal(value, state.detailDrillParam || "")}</td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  if (tbody.rows.length > maxPoints) tbody.deleteRow(tbody.rows.length - 1);
}

// ============================================================
//  AI ANALYST — chat interface
// ============================================================

let _chatHistory   = [];   // [{role, content}]
let _chatStreaming  = false;

const _CHAT_STORAGE_KEY = "aura_chat_v1";
const _CHAT_MAX_STORED  = 40;   // messages (20 turns)

function _saveChatHistory() {
  try {
    const trimmed = _chatHistory.slice(-_CHAT_MAX_STORED);
    localStorage.setItem(_CHAT_STORAGE_KEY, JSON.stringify(trimmed));
  } catch (_) {}
}

function _loadChatHistory() {
  try {
    const raw = localStorage.getItem(_CHAT_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

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

  // Restore previous session from localStorage
  const saved = _loadChatHistory();
  if (saved.length) {
    _chatHistory = saved;
    saved.forEach(m => _appendMessage(m.role === "user" ? "user" : "ai", m.content));
  }

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
  try { localStorage.removeItem(_CHAT_STORAGE_KEY); } catch (_) {}
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
      _saveChatHistory();
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
        state.detailPendingDrill = t.param;
        document.querySelector('.tab[data-page="detail"]').click();
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

// ============================================================
//  SETTINGS PAGE
// ============================================================

const _SETTINGS_TOKEN_KEY = "aura_settings_token";
let _exportMaxId = null;
let _wsKeepaliveTimer = null;

const _SETTINGS_DEFAULTS = {
  // Alerts
  alert_min_consecutive:   10,
  alert_cooldown_seconds:  600,
  alert_critical_rf_gate:  0.85,
  latch_threshold:         0.95,
  latch_min_consecutive:   3,
  dqn_rf_bypass_threshold: 0.92,
  // Generation
  tick_interval_seconds:   1,
  noise_scale:             1.0,
  crew_event_frequency:    "medium",
  fault_injection_enabled: true,
  // Trends
  mk_p_threshold:          0.01,
  mk_tau_advisory:         0.35,
  mk_tau_warning:          0.65,
  slope_magnitude_gate:    0.05,
  cusum_threshold:         7.0,
  cusum_baseline_pct:      0.20,
  zscore_threshold:        3.5,
  zscore_single_threshold: 4.5,
  zscore_window:           30,
  // Display
  dashboard_refresh_ms:    30000,
  chat_max_stored:         40,
  trends_default_n:        100,
  detail_default_n:        100,
};

function _settingsToken()         { return sessionStorage.getItem(_SETTINGS_TOKEN_KEY); }
function _settingsAuthHeader()    { return { "Authorization": `Bearer ${_settingsToken()}` }; }
function _settingsClearToken()    { sessionStorage.removeItem(_SETTINGS_TOKEN_KEY); }
function _settingsSaveToken(tok)  { sessionStorage.setItem(_SETTINGS_TOKEN_KEY, tok); }

async function _settingsFetch(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ..._settingsAuthHeader(), ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    _settingsClearToken();
    _showSettingsLogin();
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function _settingsExportCsv() {
  const res = await fetch("/api/settings/data/export/csv", {
    headers: { "Authorization": `Bearer ${_settingsToken()}` },
  });
  if (res.status === 401) {
    _settingsClearToken();
    _showSettingsLogin();
    throw new Error("Session expired");
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

  const maxId    = parseInt(res.headers.get("X-Export-Max-Id")    || "0");
  const rowCount = parseInt(res.headers.get("X-Export-Row-Count") || "0");
  const disp     = res.headers.get("Content-Disposition") || "";
  const fnMatch  = disp.match(/filename="([^"]+)"/);
  const filename = fnMatch ? fnMatch[1] : "aura_export.csv";

  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  return { maxId, rowCount };
}

function _showSettingsLogin() {
  $("settings-login").classList.remove("hidden");
  $("settings-panel").classList.add("hidden");
}

function _showSettingsPanel() {
  $("settings-login").classList.add("hidden");
  $("settings-panel").classList.remove("hidden");
}

function buildSettings() {
  // Login form
  $("btn-settings-login").addEventListener("click", _doSettingsLogin);
  $("settings-password").addEventListener("keydown", e => {
    if (e.key === "Enter") _doSettingsLogin();
  });

  // Lock button
  $("btn-settings-logout").addEventListener("click", async () => {
    try { await _settingsFetch("/api/auth/logout", { method: "POST" }); } catch (_) {}
    _settingsClearToken();
    _showSettingsLogin();
    $("settings-password").value = "";
  });

  // Inner tab switching
  document.querySelectorAll(".settings-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".settings-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".settings-pane").forEach(p => p.classList.add("hidden"));
      btn.classList.add("active");
      $(`stab-${btn.dataset.stab}`).classList.remove("hidden");
      if (btn.dataset.stab === "ml") _loadMlStatus();
      if (btn.dataset.stab === "integrations") _loadIntegrationStatus();
      if (btn.dataset.stab === "faults") _loadFaultStatus();
    });
  });

  // Data tab buttons
  $("btn-clear-sensor-data").addEventListener("click", async () => {
    if (!confirm("Clear ALL sensor data and reset LSTM buffers? Cannot be undone.")) return;
    try {
      await _settingsFetch("/api/settings/data/sensor", { method: "DELETE" });
      showToast("Sensor data cleared", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });
  $("btn-clear-alert-history").addEventListener("click", async () => {
    if (!confirm("Delete all alert history?")) return;
    try {
      await _settingsFetch("/api/settings/data/alerts", { method: "DELETE" });
      showToast("Alert history cleared", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });
  $("btn-clear-lstm").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/data/lstm", { method: "DELETE" });
      showToast("LSTM buffers cleared", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  // Export CSV
  $("btn-export-csv").addEventListener("click", async () => {
    const btn = $("btn-export-csv");
    btn.disabled = true;
    btn.textContent = "Exporting…";
    $("export-result").classList.add("hidden");
    try {
      const { maxId, rowCount } = await _settingsExportCsv();
      _exportMaxId = maxId;
      $("export-result-text").textContent = rowCount > 0
        ? `✓ Export complete — ${rowCount.toLocaleString()} location readings (ticks through #${maxId.toLocaleString()})`
        : "✓ Export complete — database was empty";
      $("export-result").classList.remove("hidden");
      $("btn-export-clear").disabled = maxId === 0;
    } catch (e) {
      showToast(`Export failed: ${e.message}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Export to CSV";
    }
  });
  $("btn-export-keep").addEventListener("click", () => {
    $("export-result").classList.add("hidden");
    _exportMaxId = null;
  });
  $("btn-export-clear").addEventListener("click", async () => {
    if (!confirm(
      `Delete ${_exportMaxId.toLocaleString()} ticks of exported data?\n\nReadings generated after the export started are safe.`
    )) return;
    try {
      const data = await _settingsFetch("/api/settings/data/exported", {
        method: "DELETE",
        body: JSON.stringify({ max_id: _exportMaxId }),
      });
      showToast(`Cleared ${data.deleted_rows.toLocaleString()} rows`, "success");
      $("export-result").classList.add("hidden");
      _exportMaxId = null;
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  // Alert settings save
  $("btn-save-alerts").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/alerts", {
        method: "PATCH",
        body: JSON.stringify({ updates: {
          alert_min_consecutive:  parseInt($("s-alert-min-consecutive").value),
          alert_cooldown_seconds: parseInt($("s-alert-cooldown").value),
          alert_critical_rf_gate: parseFloat($("s-alert-critical-rf").value),
          latch_threshold:        parseFloat($("s-latch-threshold").value),
          latch_min_consecutive:  parseInt($("s-latch-min-consec").value),
          dqn_rf_bypass_threshold: parseFloat($("s-dqn-bypass").value),
        }}),
      });
      showToast("Alert settings saved", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  $("btn-reset-alerts").addEventListener("click", () => {
    const d = _SETTINGS_DEFAULTS;
    $("s-alert-min-consecutive").value = d.alert_min_consecutive;
    $("s-alert-cooldown").value        = d.alert_cooldown_seconds;
    $("s-alert-critical-rf").value     = d.alert_critical_rf_gate;
    $("s-latch-threshold").value       = d.latch_threshold;
    $("s-latch-min-consec").value      = d.latch_min_consecutive;
    $("s-dqn-bypass").value            = d.dqn_rf_bypass_threshold;
    showToast("Alert fields reset to defaults — click Save to apply", "info");
  });

  // Generation settings save
  $("btn-save-generation").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/generation", {
        method: "PATCH",
        body: JSON.stringify({ updates: {
          tick_interval_seconds: parseFloat($("s-tick-interval").value),
          noise_scale:           parseFloat($("s-noise-scale").value),
          crew_event_frequency:  $("s-crew-freq").value,
          fault_injection_enabled: $("s-fault-injection").checked,
        }}),
      });
      showToast("Generation settings saved", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  $("btn-reset-generation").addEventListener("click", () => {
    const d = _SETTINGS_DEFAULTS;
    $("s-tick-interval").value      = d.tick_interval_seconds;
    $("s-noise-scale").value        = d.noise_scale;
    $("s-crew-freq").value          = d.crew_event_frequency;
    $("s-fault-injection").checked  = d.fault_injection_enabled;
    showToast("Generation fields reset to defaults — click Save to apply", "info");
  });

  // Trend settings save
  $("btn-save-trends").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/trends", {
        method: "PATCH",
        body: JSON.stringify({ updates: {
          mk_p_threshold:          parseFloat($("s-mk-p").value),
          mk_tau_advisory:         parseFloat($("s-mk-tau-advisory").value),
          mk_tau_warning:          parseFloat($("s-mk-tau-warning").value),
          slope_magnitude_gate:    parseFloat($("s-slope-gate").value),
          cusum_threshold:         parseFloat($("s-cusum-threshold").value),
          cusum_baseline_pct:      parseFloat($("s-cusum-baseline").value),
          zscore_threshold:        parseFloat($("s-zscore-threshold").value),
          zscore_single_threshold: parseFloat($("s-zscore-single").value),
          zscore_window:           parseInt($("s-zscore-window").value),
        }}),
      });
      showToast("Trend settings saved", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  $("btn-reset-trends").addEventListener("click", () => {
    const d = _SETTINGS_DEFAULTS;
    $("s-mk-p").value            = d.mk_p_threshold;
    $("s-mk-tau-advisory").value = d.mk_tau_advisory;
    $("s-mk-tau-warning").value  = d.mk_tau_warning;
    $("s-slope-gate").value      = d.slope_magnitude_gate;
    $("s-cusum-threshold").value = d.cusum_threshold;
    $("s-cusum-baseline").value  = d.cusum_baseline_pct;
    $("s-zscore-threshold").value  = d.zscore_threshold;
    $("s-zscore-single").value     = d.zscore_single_threshold;
    $("s-zscore-window").value     = d.zscore_window;
    showToast("Trend fields reset to defaults — click Save to apply", "info");
  });

  // Display settings save
  $("btn-save-display").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/display", {
        method: "PATCH",
        body: JSON.stringify({ updates: {
          mission_start_iso:   $("s-mission-start").value || null,
          dashboard_refresh_ms: parseInt($("s-dashboard-refresh").value),
          chat_max_stored:     parseInt($("s-chat-max").value),
          trends_default_n:    parseInt($("s-trends-n").value),
          detail_default_n:    parseInt($("s-detail-n").value),
        }}),
      });
      showToast("Display settings saved", "success");
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  $("btn-reset-display").addEventListener("click", () => {
    const d = _SETTINGS_DEFAULTS;
    $("s-mission-start").value     = "";
    $("s-dashboard-refresh").value = d.dashboard_refresh_ms;
    $("s-chat-max").value          = d.chat_max_stored;
    $("s-trends-n").value          = d.trends_default_n;
    $("s-detail-n").value          = d.detail_default_n;
    showToast("Display fields reset to defaults — click Save to apply", "info");
  });

  // Groq key
  $("btn-groq-show").addEventListener("click", () => {
    const inp = $("s-groq-key");
    inp.type = inp.type === "password" ? "text" : "password";
    $("btn-groq-show").textContent = inp.type === "password" ? "Show" : "Hide";
  });
  $("btn-save-groq").addEventListener("click", async () => {
    try {
      await _settingsFetch("/api/settings/integrations/groq", {
        method: "PATCH",
        body: JSON.stringify({ updates: { groq_api_key: $("s-groq-key").value } }),
      });
      showToast("Groq API key saved", "success");
      $("s-groq-key").value = "";
      $("groq-status-badge").textContent = "saved";
      $("groq-status-badge").className = "integration-badge ok";
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });
  $("btn-test-groq").addEventListener("click", async () => {
    const res = $("groq-test-result");
    res.textContent = "Testing…";
    res.className = "integration-result";
    res.classList.remove("hidden");
    try {
      const data = await _settingsFetch("/api/settings/integrations/groq/test", { method: "POST", body: JSON.stringify({updates:{}}) });
      if (data.ok) {
        res.textContent = "✓ Connection successful";
        res.className = "integration-result ok";
        $("groq-status-badge").textContent = "connected";
        $("groq-status-badge").className = "integration-badge ok";
      } else {
        res.textContent = `✗ ${data.error}`;
        res.className = "integration-result error";
        $("groq-status-badge").textContent = "error";
        $("groq-status-badge").className = "integration-badge error";
      }
    } catch (e) {
      res.textContent = `Error: ${e.message}`;
      res.className = "integration-result error";
    }
  });

  // Password change
  $("btn-change-password").addEventListener("click", async () => {
    const result = $("pw-change-result");
    const newPw  = $("s-pw-new").value;
    const confirm = $("s-pw-confirm").value;
    if (newPw !== confirm) {
      result.textContent = "New passwords do not match";
      result.className = "settings-result error";
      result.classList.remove("hidden");
      return;
    }
    if (newPw.length < 6) {
      result.textContent = "Password must be at least 6 characters";
      result.className = "settings-result error";
      result.classList.remove("hidden");
      return;
    }
    try {
      await _settingsFetch("/api/settings/security/change-password", {
        method: "POST",
        body: JSON.stringify({ current: $("s-pw-current").value, new_password: newPw }),
      });
      result.textContent = "✓ Password changed successfully";
      result.className = "settings-result ok";
      result.classList.remove("hidden");
      $("s-pw-current").value = "";
      $("s-pw-new").value = "";
      $("s-pw-confirm").value = "";
    } catch (e) {
      result.textContent = e.message.includes("400") ? "Current password incorrect" : `Error: ${e.message}`;
      result.className = "settings-result error";
      result.classList.remove("hidden");
    }
  });

  // Revoke all sessions
  $("btn-revoke-sessions").addEventListener("click", async () => {
    if (!confirm("This will log out ALL active sessions including yours. Continue?")) return;
    try {
      await _settingsFetch("/api/settings/security/revoke-all", { method: "POST", body: JSON.stringify({}) });
    } catch (_) {}
    _settingsClearToken();
    _showSettingsLogin();
    showToast("All sessions revoked", "info");
  });

  // Faults tab
  $("btn-s-refresh-faults").addEventListener("click", _loadFaultStatus);
  $("s-inject-fault").addEventListener("change", _updateFaultDesc);
  $("btn-s-inject").addEventListener("click", async () => {
    try {
      await injectFault();
      _loadFaultStatus();
    } catch (_) {}
  });
  $("btn-s-clear-all-faults").addEventListener("click", async () => {
    if (!confirm("Clear all injected faults?")) return;
    try {
      await apiDelete("/api/faults");
      showToast("All faults cleared", "success");
      await refreshLocationStates();
      _loadFaultStatus();
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });
  // Resolve individual fault via event delegation
  $("s-active-faults-table").addEventListener("click", async e => {
    const btn = e.target.closest("[data-resolve]");
    if (!btn) return;
    const loc = btn.dataset.resolve;
    try {
      await apiDelete(`/api/faults/${encodeURIComponent(loc)}`);
      showToast(`Fault resolved: ${loc}`, "success");
      await refreshLocationStates();
      _loadFaultStatus();
    } catch (e) { showToast(`Error: ${e.message}`, "error"); }
  });

  // If we already have a token from this session, go straight to panel
  if (_settingsToken()) {
    _showSettingsPanel();
    _loadSettingsValues();
  }
}

async function _doSettingsLogin() {
  const pw  = $("settings-password").value;
  const err = $("settings-login-error");
  err.classList.add("hidden");
  try {
    const data = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    }).then(async r => {
      if (!r.ok) throw new Error("Invalid password");
      return r.json();
    });
    _settingsSaveToken(data.token);
    $("settings-password").value = "";
    _showSettingsPanel();
    _loadSettingsValues();
  } catch (e) {
    err.textContent = "Invalid password";
    err.classList.remove("hidden");
  }
}

async function _loadSettingsValues() {
  try {
    const s = await _settingsFetch("/api/settings");
    // Alerts
    $("s-alert-min-consecutive").value = s.alert_min_consecutive;
    $("s-alert-cooldown").value        = s.alert_cooldown_seconds;
    $("s-alert-critical-rf").value     = s.alert_critical_rf_gate;
    $("s-latch-threshold").value       = s.latch_threshold;
    $("s-latch-min-consec").value      = s.latch_min_consecutive;
    $("s-dqn-bypass").value            = s.dqn_rf_bypass_threshold;
    // Generation
    $("s-tick-interval").value  = s.tick_interval_seconds;
    $("s-noise-scale").value    = s.noise_scale;
    $("s-crew-freq").value      = s.crew_event_frequency;
    $("s-fault-injection").checked = s.fault_injection_enabled;
    // Trends
    $("s-mk-p").value            = s.mk_p_threshold;
    $("s-mk-tau-advisory").value = s.mk_tau_advisory;
    $("s-mk-tau-warning").value  = s.mk_tau_warning;
    $("s-slope-gate").value      = s.slope_magnitude_gate;
    $("s-cusum-threshold").value = s.cusum_threshold;
    $("s-cusum-baseline").value  = s.cusum_baseline_pct;
    $("s-zscore-threshold").value = s.zscore_threshold;
    $("s-zscore-single").value   = s.zscore_single_threshold;
    $("s-zscore-window").value   = s.zscore_window;
    // Display
    $("s-mission-start").value      = s.mission_start_iso || "";
    $("s-dashboard-refresh").value  = s.dashboard_refresh_ms;
    $("s-chat-max").value           = s.chat_max_stored;
    $("s-trends-n").value           = s.trends_default_n;
    $("s-detail-n").value           = s.detail_default_n;
    // Integrations
    const badge = $("groq-status-badge");
    if (s.groq_key_set) {
      badge.textContent = "key saved";
      badge.className = "integration-badge ok";
    } else {
      badge.textContent = "not configured";
      badge.className = "integration-badge missing";
    }
  } catch (e) {
    showToast(`Failed to load settings: ${e.message}`, "error");
  }
}

async function _loadMlStatus() {
  const container = $("ml-status-cards");
  container.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const s = await _settingsFetch("/api/settings/ml/status");
    const skMatch = s.sklearn_version === "1.8.0";
    container.innerHTML = `
      <div class="ml-status-card">
        <div class="ml-status-card-name">Isolation Forest + Random Forest</div>
        <div class="ml-status-card-row ${s.ml_enabled ? "ok" : "warn"}">${s.ml_enabled ? "✓ Loaded" : "✗ Not loaded"}</div>
        <div class="ml-status-card-row ${skMatch ? "ok" : "warn"}">sklearn ${s.sklearn_version}${!skMatch ? " ⚠ Trained on 1.8.0 — retrain recommended" : ""}</div>
      </div>
      <div class="ml-status-card">
        <div class="ml-status-card-name">LSTM Predictor</div>
        <div class="ml-status-card-row ${s.lstm_enabled ? "ok" : "warn"}">${s.lstm_enabled ? "✓ Loaded" : "✗ Not loaded"}</div>
      </div>
      <div class="ml-status-card">
        <div class="ml-status-card-name">DQN Recommender</div>
        <div class="ml-status-card-row ${s.dqn_enabled ? "ok" : "warn"}">${s.dqn_enabled ? "✓ Loaded" : "✗ Not loaded"}</div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

async function _loadIntegrationStatus() {
  try {
    const s = await _settingsFetch("/api/settings");
    const badge = $("groq-status-badge");
    if (s.groq_key_set) {
      badge.textContent = "key saved";
      badge.className = "integration-badge ok";
    } else {
      badge.textContent = "not configured";
      badge.className = "integration-badge missing";
    }
  } catch (_) {}
}

async function _loadFaultStatus() {
  const container = $("s-active-faults-table");
  container.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const states = await apiFetch("/api/locations");
    const rows = Object.entries(states).map(([loc, s]) => {
      const hasFault = s.active_fault;
      return `<tr>
        <td>${loc}</td>
        <td class="${hasFault ? "fault-active" : "fault-none"}">${hasFault || "—"}</td>
        <td>${hasFault
          ? `<button class="btn btn-ghost btn-sm" data-resolve="${loc}">Resolve</button>`
          : ""}</td>
      </tr>`;
    }).join("");
    container.innerHTML = `
      <table>
        <thead><tr><th>Location</th><th>Active Fault</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    container.innerHTML = `<div class="loading">Error: ${e.message}</div>`;
  }
}

function _updateFaultDesc() {
  const fault = $("s-inject-fault").value;
  const panel = $("s-fault-desc");
  if (!fault || !state.config) { panel.classList.add("hidden"); return; }
  const precursor = state.config.fault_precursor_hours?.[fault];
  const impacts   = state.config.fault_impacts?.[fault] || [];
  panel.innerHTML = `
    <div class="fdp-name">${fault}</div>
    <div class="fdp-row">Precursor window: <span>${precursor != null ? precursor + " h" : "—"}</span></div>
    <div class="fdp-row">Affected parameters: <span>${impacts.join(", ") || "—"}</span></div>`;
  panel.classList.remove("hidden");
}

// ── Start ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", boot);
