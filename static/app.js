/* Subway Tracker frontend: search -> routes -> picker -> tracking -> done.
   State is server-driven: poll /api/journeys/current and render whatever
   state the backend reports, so a reloaded phone resumes mid-journey. */

const $ = (id) => document.getElementById(id);
const views = ["search", "routes", "picker", "track", "done"];

let itineraries = [];
let map, trainMarker, routeLine, pathLine;
let pollTimer = null;
let arrivalsTimer = null;

function show(view) {
  views.forEach((v) => $(`view-${v}`).classList.toggle("hidden", v !== view));
  if (view !== "track") stopMapPolling();
  if (view !== "picker") stopArrivalsPolling();
}

async function api(path, opts = {}) {
  let resp;
  try {
    resp = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch {
    // Safari reports any network-level failure as bare "Load failed"
    throw new Error(
      `서버(${location.origin})에 연결할 수 없어요. ` +
      "uvicorn이 실행 중인지, 주소/포트가 맞는지 확인하세요."
    );
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

if (location.protocol === "file:") {
  document.addEventListener("DOMContentLoaded", () => {
    $("search-error").textContent =
      "index.html을 파일로 직접 열면 API를 호출할 수 없어요. " +
      "서버를 실행하고 http://localhost:8000 으로 접속하세요.";
  });
}

/* ---------- 1. station search ---------- */

function wireAutocomplete(inputId, listId) {
  const input = $(inputId);
  const list = $(listId);
  let t;
  let seq = 0;
  input.addEventListener("input", () => {
    clearTimeout(t);
    const q = input.value.trim();
    if (!q) { list.innerHTML = ""; return; }
    t = setTimeout(async () => {
      const mySeq = ++seq;
      try {
        const stations = await api(`/stations/search?q=${encodeURIComponent(q)}`);
        if (mySeq !== seq) return; // a newer query is in flight
        // one row per station+line; picking pins that line's exact coordinates
        list.innerHTML = "";
        stations.forEach((s) => {
          const li = document.createElement("li");
          li.innerHTML = `${s.name}<span>${s.line}</span>`;
          li.onclick = () => {
            input.value = s.name;
            input.dataset.stationId = s.station_id;
            list.innerHTML = "";
          };
          list.appendChild(li);
        });
      } catch { /* ignore autocomplete errors */ }
    }, 250);
  });
  // typing again invalidates the previous pick
  input.addEventListener("input", () => { delete input.dataset.stationId; });
}

wireAutocomplete("start-input", "start-suggest");
wireAutocomplete("end-input", "end-suggest");

$("search-btn").onclick = async () => {
  $("search-error").textContent = "";
  const start = $("start-input").value.trim();
  const end = $("end-input").value.trim();
  if (!start || !end) { $("search-error").textContent = "출발역과 도착역을 입력하세요"; return; }
  $("search-btn").disabled = true;
  try {
    itineraries = await api("/routes", {
      method: "POST",
      body: JSON.stringify({
        start, end,
        start_id: $("start-input").dataset.stationId || null,
        end_id: $("end-input").dataset.stationId || null,
      }),
    });
    renderRoutes();
    show("routes");
  } catch (e) {
    $("search-error").textContent = e.message;
  } finally {
    $("search-btn").disabled = false;
  }
};

/* ---------- 2. route list ---------- */

function renderRoutes() {
  const box = $("route-list");
  box.innerHTML = "";
  itineraries.forEach((it, i) => {
    const div = document.createElement("div");
    div.className = "card route-card";
    const mins = Math.round(it.total_time / 60);
    const uncovered = it.legs.some((l) => !l.line_key);
    div.innerHTML = `
      <div class="time">${mins}분 ${uncovered ? '<span class="badge">일부 실시간 미지원</span>' : ""}</div>
      <div class="meta">환승 ${it.transfer_count}회 · 도보 ${Math.round(it.total_walk_time / 60)}분${it.fare ? ` · ${it.fare.toLocaleString()}원` : ""}</div>
      <div class="legs">${it.summary.join("<br>")}</div>`;
    div.onclick = () => selectRoute(i);
    box.appendChild(div);
  });
}

$("routes-back").onclick = () => show("search");

async function selectRoute(i) {
  try {
    await api("/journeys", { method: "POST", body: JSON.stringify({ itinerary: itineraries[i] }) });
    refresh();
  } catch (e) { alert(e.message); }
}

/* ---------- 3. train picker ---------- */

async function loadArrivals(snap) {
  $("picker-title").textContent = `열차 선택 — ${snap.leg.start} 출발`;
  $("picker-sub").textContent = `${snap.leg.route} · ${snap.leg.start} → ${snap.leg.end} (${snap.leg_idx + 1}/${snap.leg_count}구간)`;
  const list = $("train-list");
  if (!snap.leg.covered) {
    list.innerHTML = `<div class="card">이 노선은 실시간 위치를 제공하지 않아요.<br>탑승 후 아래 버튼을 눌러 시간 기반 추적을 시작하세요.</div>`;
    $("picker-timer-board").classList.remove("hidden");
    return;
  }
  $("picker-timer-board").classList.add("hidden");
  try {
    const data = await api("/journeys/current/arrivals");
    list.innerHTML = "";
    if (!data.trains.length) {
      list.innerHTML = `<div class="card">접근 중인 열차 정보가 없어요. 잠시 후 새로고침하세요.</div>`;
      return;
    }
    data.trains.forEach((t) => {
      const div = document.createElement("div");
      div.className = "card train-card" + (t.matches_direction ? "" : " dim");
      const eta = t.eta_seconds > 0 ? `${Math.max(1, Math.round(t.eta_seconds / 60))}분` : "곧 도착";
      div.innerHTML = `
        <div><span class="no">${t.train_no}편성</span>${t.is_express ? '<span class="badge">급행</span>' : ""}
        ${t.matches_direction ? "" : '<span class="badge">방향 확인</span>'}<span class="eta">${eta}</span></div>
        <div class="dir">${t.direction_label} · ${t.terminus}행</div>
        <div class="dir">${t.arrival_msg}</div>`;
      div.onclick = () => boardTrain(t.train_no);
      list.appendChild(div);
    });
  } catch (e) {
    list.innerHTML = `<div class="card error">${e.message}</div>`;
  }
}

async function boardTrain(trainNo) {
  try {
    await api("/journeys/current/board", { method: "POST", body: JSON.stringify({ train_no: trainNo }) });
    refresh();
  } catch (e) { alert(e.message); }
}

$("picker-refresh").onclick = refresh;
$("picker-timer-board").onclick = () => boardTrain(null);
$("picker-cancel").onclick = cancelJourney;

function startArrivalsPolling(snap) {
  stopArrivalsPolling();
  arrivalsTimer = setInterval(() => loadArrivals(snap), 15000);
}
function stopArrivalsPolling() { clearInterval(arrivalsTimer); arrivalsTimer = null; }

/* ---------- 4. tracking map ---------- */

function ensureMap() {
  if (map) return;
  map = L.map("map");
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap",
  }).addTo(map);
}

function renderTrack(snap) {
  ensureMap();
  const coords = (snap.leg.shape && snap.leg.shape.length)
    ? snap.leg.shape
    : snap.leg.stations.map((s) => [s.lat, s.lon]);
  if (!routeLine) {
    routeLine = L.polyline(coords, { color: "#1c3f94", weight: 4, opacity: 0.5 }).addTo(map);
    map.fitBounds(routeLine.getBounds(), { padding: [30, 30] });
    snap.leg.stations.forEach((s) =>
      L.circleMarker([s.lat, s.lon], { radius: 4, color: "#1c3f94", fillOpacity: 1 })
        .addTo(map).bindTooltip(s.name));
  }
  $("track-leg").textContent = `${snap.leg.route} · ${snap.leg.start} → ${snap.leg.end} (${snap.leg_idx + 1}/${snap.leg_count}구간)`;
  const t = snap.train;
  if (t) {
    if (!trainMarker) {
      trainMarker = L.marker([t.lat, t.lon]).addTo(map);
    } else {
      trainMarker.setLatLng([t.lat, t.lon]);
    }
    const modeLabel = snap.tracking_mode === "timer" ? " (시간 기반 추정)" : "";
    const statusKo = { approaching: "진입 중", arrived: "도착", departed: "출발",
      between: "이동 중", estimated: "추정 위치", before_leg: "탑승역으로 오는 중" }[t.status] || t.status;
    $("track-status").textContent = `열차 ${t.train_no} · ${t.station_name} ${statusKo}${modeLabel}`;
  } else {
    $("track-status").textContent = "열차 위치 수신 대기 중…";
  }
  $("track-points").textContent = `기록된 위치 ${snap.point_count}개`;
}

function startMapPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(refresh, 5000);
}
function stopMapPolling() { clearInterval(pollTimer); pollTimer = null; }

$("alight-btn").onclick = async () => {
  try { await api("/journeys/current/alight", { method: "POST" }); refresh(); }
  catch (e) { alert(e.message); }
};
$("missed-btn").onclick = async () => {
  try { await api("/journeys/current/missed", { method: "POST" }); refresh(); }
  catch (e) { alert(e.message); }
};
$("track-cancel").onclick = cancelJourney;

async function cancelJourney() {
  if (!confirm("여정을 취소할까요? 기록은 Reitti로 전송되지 않아요.")) return;
  try { await api("/journeys/current/cancel", { method: "POST" }); } catch { /* already gone */ }
  resetMap();
  show("search");
}

function resetMap() {
  if (map) { map.remove(); map = null; }
  trainMarker = null; routeLine = null; pathLine = null;
}

/* ---------- 5. done ---------- */

$("retry-push-btn").onclick = async () => {
  try { await api("/journeys/current/retry-push", { method: "POST" }); refresh(); }
  catch (e) { alert(e.message); }
};
$("new-journey-btn").onclick = () => { resetMap(); show("search"); };

/* ---------- state-driven render loop ---------- */

let lastLegIdx = null;

async function refresh() {
  let snap;
  try { snap = await api("/journeys/current"); }
  catch { return; }

  switch (snap.state) {
    case "awaiting_board":
      if (lastLegIdx !== snap.leg_idx) resetMap();  // transfer: new leg map
      lastLegIdx = snap.leg_idx;
      show("picker");
      loadArrivals(snap);
      startArrivalsPolling(snap);
      break;
    case "on_train":
      if (lastLegIdx !== snap.leg_idx) resetMap();
      lastLegIdx = snap.leg_idx;
      show("track");
      renderTrack(snap);
      startMapPolling();
      break;
    case "completed":
      show("done");
      $("done-title").textContent = "🎉 여정 완료";
      $("done-msg").textContent = `위치 ${snap.point_count}개를 Reitti로 전송했어요.`;
      $("retry-push-btn").classList.add("hidden");
      break;
    case "push_failed":
      show("done");
      $("done-title").textContent = "⚠️ Reitti 전송 실패";
      $("done-msg").textContent = snap.error || "전송 중 오류가 발생했어요.";
      $("retry-push-btn").classList.remove("hidden");
      break;
    default:
      show("search");
  }
}

refresh();
