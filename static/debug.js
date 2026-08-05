const $ = (id) => document.getElementById(id);

let debugMap;
let journeys = [];
let layers = [];
let pointMarkers = [];

function ensureDebugMap() {
  if (debugMap) return;
  debugMap = L.map("debug-map").setView([37.5665, 126.9780], 11);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(debugMap);
}

function clearLayers() {
  layers.forEach((layer) => layer.remove());
  layers = [];
  pointMarkers = [];
}

function addLayer(layer) {
  layers.push(layer.addTo(debugMap));
  return layer;
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[char]));
}

function renderOptions() {
  const select = $("journey-select");
  select.innerHTML = "";
  journeys.forEach((journey, index) => {
    const option = document.createElement("option");
    option.value = index;
    option.textContent = `#${journey.journey_id} · ${journey.state} · ${journey.points.length} points · ${formatDate(journey.created_at)}`;
    select.appendChild(option);
  });
}

function renderJourney(journey) {
  ensureDebugMap();
  clearLayers();
  const retryButton = $("debug-retry-reitti");
  retryButton.disabled = !journey || !journey.can_retry;

  if (!journey) {
    $("debug-summary").innerHTML = "No journeys found in tracker.db.";
    $("debug-status").textContent = "No location data available.";
    $("debug-timeline").innerHTML = "";
    debugMap.setView([37.5665, 126.9780], 11);
    return;
  }

  const bounds = [];

  journey.legs.forEach((leg) => {
    const routeCoords = leg.shape?.length
      ? leg.shape
      : leg.stations.map((station) => [station.lat, station.lon]);
    if (routeCoords.length > 1) {
      addLayer(L.polyline(routeCoords, {
        color: "#1c3f94",
        weight: 4,
        opacity: 0.35,
      }).bindTooltip(`${leg.idx + 1}. ${leg.route}: ${leg.start} → ${leg.end}`));
      routeCoords.forEach((coord) => bounds.push(coord));
    }
    leg.stations.forEach((station) => {
      const marker = L.circleMarker([station.lat, station.lon], {
        radius: 4,
        color: "#1c3f94",
        fillColor: "#fff",
        fillOpacity: 1,
        weight: 2,
      }).bindTooltip(`${station.name} (${leg.route})`);
      addLayer(marker);
      bounds.push([station.lat, station.lon]);
    });
  });

  const pointCoords = journey.points.map((point) => [point.lat, point.lon]);
  if (pointCoords.length > 1) {
    addLayer(L.polyline(pointCoords, {
      color: "#16a34a",
      weight: 5,
      opacity: 0.9,
    }).bindTooltip("logged points"));
  }
  journey.points.forEach((point, index) => {
    const marker = L.circleMarker([point.lat, point.lon], {
      radius: index === 0 || index === journey.points.length - 1 ? 7 : 4,
      color: point.estimated ? "#d97706" : "#16a34a",
      fillColor: point.estimated ? "#f59e0b" : "#22c55e",
      fillOpacity: 0.85,
      weight: 2,
    }).bindPopup(`
      <strong>${point.estimated ? "Estimated" : "Actual"} point</strong><br>
      Leg ${point.leg_idx + 1}<br>
      ${formatDate(point.ts)}<br>
      ${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}
    `);
    addLayer(marker);
    pointMarkers.push(marker);
    bounds.push([point.lat, point.lon]);
  });

  renderTimeline(journey);

  if (bounds.length) {
    debugMap.fitBounds(bounds, { padding: [30, 30] });
  } else {
    debugMap.setView([37.5665, 126.9780], 11);
  }

  const firstPoint = journey.points[0];
  const lastPoint = journey.points[journey.points.length - 1];
  $("debug-summary").innerHTML = `
    <h2>Journey #${journey.journey_id}</h2>
    <p class="sub">${escapeHtml(journey.state)}${journey.train_no ? ` · train ${escapeHtml(journey.train_no)}` : ""}${journey.tracking_mode ? ` · ${escapeHtml(journey.tracking_mode)}` : ""}</p>
    <p>${journey.summary.map(escapeHtml).join("<br>")}</p>
    <p class="sub">${journey.points.length} logged points${firstPoint ? ` · ${formatDate(firstPoint.ts)} → ${formatDate(lastPoint.ts)}` : ""}</p>
  `;
  $("debug-status").textContent = `Loaded ${journeys.length} journey(s) from tracker.db.`;
}

function renderTimeline(journey) {
  const el = $("debug-timeline");
  const points = journey.points;
  if (!points.length) {
    el.innerHTML = "<p class=\"sub\">No logged points.</p>";
    return;
  }

  const minTs = points[0].ts;
  const maxTs = points[points.length - 1].ts;
  const span = maxTs - minTs || 1;

  const dots = points.map((point, index) => {
    const pct = ((point.ts - minTs) / span) * 100;
    const cls = point.estimated ? "estimated" : "actual";
    return `<div class="timeline-dot ${cls}" style="left:${pct}%"
      title="${point.estimated ? "Estimated" : "Actual"} · ${formatDate(point.ts)}"
      data-index="${index}"></div>`;
  }).join("");

  el.innerHTML = `
    <div class="timeline-track">
      <div class="timeline-line"></div>
      ${dots}
    </div>
    <div class="timeline-labels">
      <span>${formatDate(minTs)}</span>
      <span>${formatDate(maxTs)}</span>
    </div>
  `;

  el.querySelectorAll(".timeline-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      const index = Number(dot.dataset.index);
      const marker = pointMarkers[index];
      if (!marker) return;
      debugMap.panTo(marker.getLatLng());
      marker.openPopup();
    });
  });
}

async function loadDebugLocations() {
  ensureDebugMap();
  $("debug-status").textContent = "Loading logged locations…";
  try {
    const response = await fetch("/api/debug/locations?limit=50");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    journeys = payload.journeys || [];
    renderOptions();
    renderJourney(journeys[0]);
  } catch (error) {
    $("debug-status").textContent = `Failed to load locations: ${error.message}`;
  }
}

function selectedJourney() {
  return journeys[Number($("journey-select").value)];
}

async function retrySelectedJourneyPush() {
  const journey = selectedJourney();
  if (!journey || !journey.can_retry) {
    return;
  }

  const pointCount = journey.points.length;
  const approved = window.confirm(
    `여정 #${journey.journey_id}의 위치 ${pointCount}개를 Reitti 서버로 다시 전송합니다.\n\n`
      + "SQLite에 전송 중 상태를 기록한 뒤, 보관된 위치 데이터를 Reitti로 다시 보냅니다. 계속할까요?",
  );
  if (!approved) return;

  const retryButton = $("debug-retry-reitti");
  retryButton.disabled = true;
  $("debug-status").textContent = "Reitti 재전송을 요청하고 SQLite 상태를 갱신하고 있어요…";
  try {
    const response = await fetch(`/api/debug/journeys/${journey.journey_id}/retry-push`, { method: "POST" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await loadDebugLocations();
    $("debug-status").textContent = "Reitti 재전송을 시작했어요. SQLite의 전송 상태를 새로고쳤습니다.";
  } catch (error) {
    $("debug-status").textContent = `Reitti 재전송을 시작하지 못했어요: ${error.message}`;
    renderJourney(journey);
  }
}

$("journey-select").addEventListener("change", (event) => {
  renderJourney(journeys[Number(event.target.value)]);
});
$("debug-refresh").addEventListener("click", loadDebugLocations);
$("debug-retry-reitti").addEventListener("click", () => void retrySelectedJourneyPush());

loadDebugLocations();
