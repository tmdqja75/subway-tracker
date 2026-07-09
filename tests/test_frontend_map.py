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
process.stdout.write(JSON.stringify({
  markerCount: markerCalls.length,
  coords: marker?.coords,
  iconHtml: marker?.opts?.icon?.html,
  iconClass: marker?.opts?.icon?.className,
  tooltip: marker?.tooltip,
  statusText: elements["track-status"].textContent,
}));
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


def test_selected_subway_position_uses_emoji_marker_for_approaching_and_boarded_train():
    result = run_frontend_harness()

    assert result["markerCount"] == 1, "subway marker should be reused as the train moves"
    assert result["coords"] == [37.501, 127.03]
    assert "🚇" in result["iconHtml"]
    assert result["iconClass"] == "subway-position-marker"
    assert "열차 2001" in result["tooltip"]
    assert "이동 중" in result["tooltip"]
    assert "열차 2001 · 역삼 이동 중" in result["statusText"]


def test_frontend_static_assets_are_cache_busted():
    index = Path("static/index.html").read_text()

    assert 'href="style.css?v=' in index
    assert 'src="app.js?v=' in index
