import json
import subprocess
from pathlib import Path


FRONTEND_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[1];
const code = fs.readFileSync(scriptPath, "utf8");
const elements = {};
const markerCalls = [];

function makeElement(id) {
  return {
    id,
    dataset: {},
    textContent: "",
    innerHTML: "",
    disabled: false,
    style: {},
    value: "",
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    addEventListener() {},
    appendChild() {},
  };
}

const context = {
  console,
  location: { protocol: "http:", origin: "http://localhost:8000" },
  alert() {},
  confirm() { return true; },
  setInterval() { return 1; },
  clearInterval() {},
  fetch: async () => ({ ok: true, json: async () => ({ state: "idle" }) }),
  document: {
    getElementById(id) {
      if (!elements[id]) elements[id] = makeElement(id);
      return elements[id];
    },
    addEventListener() {},
    createElement(tag) { return makeElement(tag); },
  },
  L: {
    map() {
      return {
        fitBounds() {},
        remove() {},
        setView() {},
      };
    },
    tileLayer() { return { addTo() { return this; } }; },
    polyline(coords, opts) {
      return {
        coords,
        opts,
        addTo() { return this; },
        getBounds() { return coords; },
        bindTooltip() { return this; },
      };
    },
    circleMarker(coords, opts) {
      return {
        coords,
        opts,
        addTo() { return this; },
        bindTooltip() { return this; },
      };
    },
    divIcon(opts) { return { ...opts, iconType: "divIcon" }; },
    marker(coords, opts = {}) {
      const marker = {
        coords,
        opts,
        tooltip: null,
        addTo() { markerCalls.push(this); return this; },
        setLatLng(next) { this.coords = next; return this; },
        setIcon(icon) { this.opts.icon = icon; return this; },
        bindTooltip(text) { this.tooltip = text; return this; },
      };
      return marker;
    },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(code, context, { filename: scriptPath });

const baseSnap = {
  state: "on_train",
  leg_idx: 0,
  leg_count: 1,
  tracking_mode: "realtime",
  point_count: 3,
  leg: {
    route: "수도권2호선",
    start: "강남",
    end: "사당",
    shape: [[37.498, 127.0277], [37.4766, 126.9816]],
    stations: [
      { name: "강남", lat: 37.498, lon: 127.0277 },
      { name: "사당", lat: 37.4766, lon: 126.9816 },
    ],
  },
};

vm.runInContext(`
  renderTrack(${JSON.stringify(baseSnap).replace(/</g, "\\u003c")});
  renderTrack(${JSON.stringify({
    ...baseSnap,
    train: {
      train_no: "2001",
      station_name: "강남",
      station_index: null,
      status: "before_leg",
      lat: 37.498,
      lon: 127.0277,
      updated_at: 1,
    },
  }).replace(/</g, "\\u003c")});
  renderTrack(${JSON.stringify({
    ...baseSnap,
    train: {
      train_no: "2001",
      station_name: "역삼",
      station_index: 0,
      status: "between",
      lat: 37.501,
      lon: 127.030,
      updated_at: 2,
    },
  }).replace(/</g, "\\u003c")});
`, context);

const marker = markerCalls[0];
vm.runInContext(`
  renderTransferFailure({
    reason: "authentication",
    message: "Reitti 인증이 거부됐어요. 서버 토큰을 확인하세요.",
    detail: "Reitti auth failed (401)",
    sent_points: 1,
    total_points: 3,
    can_retry: true,
  });
`, context);
vm.runInContext(`
  renderTransfer({
    journey_id: 9,
    transfer: { sent_points: 2, total_points: 5, remaining_points: 3, progress_percent: 40 },
    trip: {
      legs: [
        {
          shape: [[37.498, 127.0277], [37.49, 127.01]],
          stations: [{ name: "강남", lat: 37.498, lon: 127.0277 }, { name: "교대", lat: 37.49, lon: 127.01 }],
          transfer_walk_shape: [[37.49, 127.01], [37.489, 127.009]],
        },
        {
          shape: [[37.489, 127.009], [37.4766, 126.9816]],
          stations: [{ name: "사당", lat: 37.489, lon: 127.009 }, { name: "서울역", lat: 37.4766, lon: 126.9816 }],
          transfer_walk_shape: [],
        },
      ],
    },
  });
  window.__transferView = {
    routeCoords: transferRouteLine.coords,
    total: document.getElementById("transfer-total").textContent,
    sent: document.getElementById("transfer-sent").textContent,
    remaining: document.getElementById("transfer-remaining").textContent,
    progress: document.getElementById("transfer-progress-text").textContent,
    progressWidth: document.getElementById("transfer-progress-bar").style.width,
  };
`, context);
process.stdout.write(JSON.stringify({
  markerCount: markerCalls.length,
  coords: marker?.coords,
  iconHtml: marker?.opts?.icon?.html,
  iconClass: marker?.opts?.icon?.className,
  tooltip: marker?.tooltip,
  statusText: elements["track-status"].textContent,
  transferMessage: elements["done-msg"].textContent,
  transferDetail: elements["done-detail"].textContent,
  transferTechnicalDetail: elements["done-technical-detail"].textContent,
  transferView: context.__transferView,
}));
"""

AUTOCOMPLETE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[1];
const code = fs.readFileSync(scriptPath, "utf8");
const elements = {};
let pendingTimer = null;

function makeElement(id) {
  return {
    id,
    dataset: {},
    textContent: "",
    innerHTML: "",
    disabled: false,
    style: {},
    value: "",
    children: [],
    listeners: {},
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    addEventListener(type, fn) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(fn);
    },
    appendChild(child) { this.children.push(child); },
  };
}

const context = {
  console,
  location: { protocol: "http:", origin: "http://localhost:8000" },
  alert() {},
  confirm() { return true; },
  setInterval() { return 1; },
  clearInterval() {},
  setTimeout(fn) { pendingTimer = fn; return 1; },
  clearTimeout() {},
  fetch: async (url) => ({
    ok: true,
    json: async () => url.includes("/api/stations/search") ? [
      { station_id: "gangnam-2", name: "강남", line: "2호선", lat: 37.4980, lon: 127.0277 },
    ] : { state: "idle" },
  }),
  document: {
    getElementById(id) {
      if (!elements[id]) elements[id] = makeElement(id);
      return elements[id];
    },
    addEventListener() {},
    createElement(tag) { return makeElement(tag); },
  },
  L: {
    map() { return {}; },
    tileLayer() { return { addTo() { return this; } }; },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(code, context, { filename: scriptPath });

(async () => {
  const input = elements["start-input"];
  input.value = "강";
  input.listeners.input.forEach((fn) => fn());
  await pendingTimer();
  elements["start-suggest"].children[0].onclick();

  process.stdout.write(JSON.stringify({
    value: input.value,
    stationId: input.dataset.stationId,
  }));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""

TRAIN_PICKER_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[1];
const code = fs.readFileSync(scriptPath, "utf8");
const elements = {};

function makeElement(id) {
  return {
    id,
    dataset: {},
    textContent: "",
    innerHTML: "",
    disabled: false,
    style: {},
    value: "",
    children: [],
    className: "",
    onclick: null,
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    addEventListener() {},
    appendChild(child) { this.children.push(child); },
  };
}

const context = {
  console,
  location: { protocol: "http:", origin: "http://localhost:8000" },
  alert() {},
  confirm() { return true; },
  setInterval() { return 1; },
  clearInterval() {},
  fetch: async (url) => ({
    ok: true,
    json: async () => url.includes("/api/journeys/current/arrivals") ? {
      covered: true,
      trains: [
        {
          train_no: "2001",
          line_name: "2호선",
          terminus: "성수",
          direction_label: "성수행 - 역삼방면",
          eta_seconds: 0,
          arrival_msg: "전역 도착",
          matches_direction: true,
          is_express: false,
        },
        {
          train_no: "2002",
          line_name: "2호선",
          terminus: "성수",
          direction_label: "성수행 - 역삼방면",
          eta_seconds: 0,
          arrival_msg: "3번째 전역",
          matches_direction: true,
          is_express: false,
        },
        {
          train_no: "9999",
          line_name: "2호선",
          terminus: "신도림",
          direction_label: "신도림행 - 삼성방면",
          eta_seconds: 60,
          arrival_msg: "삼성 전역 출발",
          matches_direction: false,
          is_express: false,
        },
      ],
    } : { state: "idle" },
  }),
  document: {
    getElementById(id) {
      if (!elements[id]) elements[id] = makeElement(id);
      return elements[id];
    },
    addEventListener() {},
    createElement(tag) { return makeElement(tag); },
  },
  L: {
    map() { return {}; },
    tileLayer() { return { addTo() { return this; } }; },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(code, context, { filename: scriptPath });

(async () => {
  for (let i = 0; i < 4; i++) await Promise.resolve();  // let app.js's initial refresh settle first
  await context.loadArrivals({
    leg_idx: 0,
    leg_count: 1,
    leg: {
      covered: true,
      route: "수도권2호선",
      start: "강남",
      end: "사당",
    },
  });

  process.stdout.write(JSON.stringify({
    cards: elements["train-list"].children.map((child) => child.innerHTML),
  }));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""

ROUTE_PICKER_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const scriptPath = process.argv[1];
const code = fs.readFileSync(scriptPath, "utf8");
const elements = {};
const journeyBodies = [];

function makeElement(id) {
  return {
    id,
    dataset: {},
    textContent: "",
    innerHTML: "",
    disabled: false,
    style: {},
    value: "",
    children: [],
    className: "",
    onclick: null,
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    addEventListener() {},
    appendChild(child) { this.children.push(child); },
  };
}

const context = {
  console,
  location: { protocol: "http:", origin: "http://localhost:8000" },
  alert() {},
  confirm() { return true; },
  setInterval() { return 1; },
  clearInterval() {},
  fetch: async (url, opts = {}) => {
    if (url === "/api/journeys") journeyBodies.push(JSON.parse(opts.body));
    return { ok: true, json: async () => ({ state: "idle" }) };
  },
  document: {
    getElementById(id) {
      if (!elements[id]) elements[id] = makeElement(id);
      return elements[id];
    },
    addEventListener() {},
    createElement(tag) { return makeElement(tag); },
  },
  L: {
    map() { return {}; },
    tileLayer() { return { addTo() { return this; } }; },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(code, context, { filename: scriptPath });

(async () => {
  const routeOptions = Array.from({ length: 5 }, (_, i) => ({
    total_time: 600 + i * 60,
    transfer_count: i % 2,
    total_walk_time: 60,
    fare: 1400,
    legs: [{ route: `route-${i}`, line_key: i === 4 ? null : "2호선" }],
    summary: [`route summary ${i}`],
  }));
  vm.runInContext(`
    itineraries = ${JSON.stringify(routeOptions).replace(/</g, "\\u003c")};
    renderRoutes();
  `, context);
  await elements["route-list"].children[4].onclick();

  process.stdout.write(JSON.stringify({
    cardCount: elements["route-list"].children.length,
    selectedSummary: journeyBodies[0].itinerary.summary,
  }));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


def run_frontend_harness() -> dict:
    script = Path("static/app.js")
    completed = subprocess.run(
        ["node", "-e", FRONTEND_HARNESS, str(script)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)

def run_autocomplete_harness() -> dict:
    script = Path("static/app.js")
    completed = subprocess.run(
        ["node", "-e", AUTOCOMPLETE_HARNESS, str(script)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def run_train_picker_harness() -> dict:
    script = Path("static/app.js")
    completed = subprocess.run(
        ["node", "-e", TRAIN_PICKER_HARNESS, str(script)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def run_route_picker_harness() -> dict:
    script = Path("static/app.js")
    completed = subprocess.run(
        ["node", "-e", ROUTE_PICKER_HARNESS, str(script)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_selected_subway_position_uses_emoji_marker_for_approaching_and_boarded_train():
    result = run_frontend_harness()

    assert result["markerCount"] == 1, "subway marker should be reused as the train moves"
    assert result["coords"] == [37.501, 127.03]
    assert "🚇" in result["iconHtml"]
    assert result["iconClass"] == "subway-position-marker"
    assert "열차 2001" in result["tooltip"]
    assert "이동 중" in result["tooltip"]
    assert "열차 2001 · 역삼 이동 중" in result["statusText"]


def test_transfer_failure_view_shows_reason_progress_and_technical_detail():
    result = run_frontend_harness()

    assert result["transferMessage"] == "Reitti 인증이 거부됐어요. 서버 토큰을 확인하세요."
    assert result["transferDetail"] == "총 3개 위치 중 1개 전송됨 · 남은 기록은 기기에 보관되어 있어요."
    assert result["transferTechnicalDetail"] == "기술 정보: Reitti auth failed (401)"


def test_transfer_view_shows_live_counts_progress_and_entire_trip_route():
    result = run_frontend_harness()["transferView"]

    assert result["total"] == 5
    assert result["sent"] == 2
    assert result["remaining"] == 3
    assert result["progress"] == "40%"
    assert result["progressWidth"] == "40%"
    assert result["routeCoords"] == [
        [37.498, 127.0277],
        [37.49, 127.01],
        [37.49, 127.01],
        [37.489, 127.009],
        [37.489, 127.009],
        [37.4766, 126.9816],
    ]


def test_selected_station_displays_chosen_line_after_autocomplete_pick():
    result = run_autocomplete_harness()

    assert result["value"] == "강남 (2호선)"
    assert result["stationId"] == "gangnam-2"


def test_train_picker_uses_actual_location_when_eta_is_zero():
    result = run_train_picker_harness()

    assert len(result["cards"]) == 2
    assert "곧 도착" not in result["cards"][0]
    assert "곧 도착" not in result["cards"][1]
    assert '<span class="eta">전역 도착</span>' in result["cards"][0]
    assert '<span class="eta">3번째 전역</span>' in result["cards"][1]
    assert all("9999" not in card for card in result["cards"])


def test_train_picker_invalidates_stale_arrival_responses_and_bypasses_http_cache():
    source = Path("static/app.js").read_text()

    assert 'cache: "no-store"' in source
    assert "const requestSeq = ++arrivalsRequestSeq;" in source
    assert "if (requestSeq !== arrivalsRequestSeq) return;" in source


def test_route_picker_renders_and_selects_every_api_route_option():
    result = run_route_picker_harness()

    assert result["cardCount"] == 5
    assert result["selectedSummary"] == ["route summary 4"]


def test_frontend_static_assets_are_cache_busted():
    index = Path("static/index.html").read_text()

    assert 'href="style.css?v=' in index
    assert 'src="app.js?v=' in index
