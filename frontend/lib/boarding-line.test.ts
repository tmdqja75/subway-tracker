import { describe, expect, it } from "vitest";

import { buildBoardingLine } from "./boarding-line";
import type { ArrivingTrain, LegStation, OnboardTrain } from "./types";

const stations: LegStation[] = [
  "성신여대입구", "한성대입구", "혜화", "동대문", "동대문역사문화공원", "충무로", "명동", "회현", "서울역",
].map((name, index) => ({ index, name, lat: 0, lon: 0 }));

function arriving(overrides: Partial<ArrivingTrain>): ArrivingTrain {
  return {
    train_no: "0000",
    line_name: "4호선",
    terminus: "오이도",
    direction_label: "오이도행",
    eta_seconds: 60,
    arrival_msg: "1분 후 도착",
    status: "approaching",
    stations_away: 0,
    stations_away_estimated: false,
    matches_direction: true,
    is_express: false,
    ...overrides,
  };
}

function onboard(overrides: Partial<OnboardTrain>): OnboardTrain {
  return {
    train_no: "0000",
    line_name: "4호선",
    terminus: "오이도",
    direction_label: "오이도행",
    station_name: "동대문",
    station_index: 3,
    status: "between",
    observed_at: 0,
    matches_direction: true,
    is_express: false,
    ...overrides,
  };
}

describe("buildBoardingLine", () => {
  it("windows to 3 stations before/after the current station", () => {
    const line = buildBoardingLine(stations, "동대문역사문화공원", [], [], []);
    expect(line?.stations.map((s) => s.name)).toEqual([
      "한성대입구", "혜화", "동대문", "동대문역사문화공원", "충무로", "명동", "회현",
    ]);
    expect(line?.stations[3]).toEqual({ name: "동대문역사문화공원", isCurrent: true });
  });

  it("shrinks the window when fewer than 3 stations exist on either side", () => {
    const line = buildBoardingLine(stations, "성신여대입구", [], [], []);
    expect(line?.stations.map((s) => s.name)).toEqual(["성신여대입구", "한성대입구", "혜화", "동대문"]);
  });

  it("returns null when the current station isn't part of the leg", () => {
    expect(buildBoardingLine(stations, "존재하지않음", [], [], [])).toBeNull();
  });

  it("drops non-direction-matched and unlocatable trains", () => {
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ matches_direction: false }), arriving({ stations_away: null })],
      [onboard({ station_index: 999 })],
      [],
    );
    expect(line?.trains).toEqual([]);
  });

  it("places an approaching arriving-train a quarter before the station it's nearing", () => {
    // stations_away: 1 -> nearing 동대문(idx 3), approaching from 혜화(idx 2)
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ train_no: "4045", stations_away: 1, status: "approaching" })],
      [],
      [],
    );
    expect(line?.trains).toEqual([
      { key: "arriving-4045-0", trainNo: "4045", destination: "오이도행", isExpress: false, state: "approaching", retroactive: false, fromGapIndex: 1 },
    ]);
  });

  it("places an arrived feed train on its reported station instead of in the preceding gap", () => {
    // A train reported as arrived at 동대문 is one stop before the boarding
    // station. It belongs on the 동대문 node, not in the 혜화→동대문 segment.
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ train_no: "arrived-4045", stations_away: 1, status: "arrived" })],
      [],
      [],
    );

    expect(line?.trains).toEqual([
      { key: "arriving-arrived-4045-0", trainNo: "arrived-4045", destination: "오이도행", isExpress: false, state: "arrived", retroactive: false, atIndex: 2 },
    ]);
  });

  it("places an arrived train at the boarding station on the current node", () => {
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ train_no: "at-platform", stations_away: 0, status: "arrived" })],
      [],
      [],
    );

    expect(line?.trains).toEqual([
      { key: "arriving-at-platform-0", trainNo: "at-platform", destination: "오이도행", isExpress: false, state: "arrived", retroactive: false, atIndex: 3 },
    ]);
  });

  it("places arrived/departed/approaching onboard trains per the station-index rule", () => {
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [],
      [
        onboard({ train_no: "arrived-1", station_index: 3, status: "arrived" }),
        onboard({ train_no: "departed-1", station_index: 2, status: "departed" }),
        onboard({ train_no: "approaching-1", station_index: 5, status: "approaching" }),
      ],
      [],
    );
    expect(line?.trains).toEqual([
      { key: "onboard-arrived-1-0", trainNo: "arrived-1", destination: "오이도행", isExpress: false, state: "arrived", retroactive: true, atIndex: 2 },
      { key: "onboard-departed-1-1", trainNo: "departed-1", destination: "오이도행", isExpress: false, state: "departed", retroactive: true, fromGapIndex: 1 },
      { key: "onboard-approaching-1-2", trainNo: "approaching-1", destination: "오이도행", isExpress: false, state: "approaching", retroactive: true, fromGapIndex: 3 },
    ]);
  });

  it("prepends context-before names so trains near a leg's own first station can be placed", () => {
    // Mirrors production: leg.stations starts AT the boarding station (index
    // 0), so without context_before, currentIdx would always be 0 and no
    // "before" segment could ever exist — this is the bug context_before fixes.
    const legStartingAtBoarding: LegStation[] = ["동대문역사문화공원", "충무로"].map((name, index) => ({
      index, name, lat: 0, lon: 0,
    }));
    const contextBefore = ["혜화", "동대문"]; // farthest -> nearest, per fetch_boarding_context's contract

    const line = buildBoardingLine(
      legStartingAtBoarding,
      "동대문역사문화공원",
      [arriving({ train_no: "entering", stations_away: 0 })],
      [],
      contextBefore,
    );

    expect(line?.stations.map((s) => s.name)).toEqual(["혜화", "동대문", "동대문역사문화공원", "충무로"]);
    expect(line?.stations[2]).toEqual({ name: "동대문역사문화공원", isCurrent: true });
    expect(line?.trains).toEqual([
      { key: "arriving-entering-0", trainNo: "entering", destination: "오이도행", isExpress: false, state: "approaching", retroactive: false, fromGapIndex: 1 },
    ]);
  });
});
