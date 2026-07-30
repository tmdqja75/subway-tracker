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
    const line = buildBoardingLine(stations, "동대문역사문화공원", [], []);
    expect(line?.stations.map((s) => s.name)).toEqual([
      "한성대입구", "혜화", "동대문", "동대문역사문화공원", "충무로", "명동", "회현",
    ]);
    expect(line?.stations[3]).toEqual({ name: "동대문역사문화공원", isCurrent: true });
  });

  it("shrinks the window when fewer than 3 stations exist on either side", () => {
    const line = buildBoardingLine(stations, "성신여대입구", [], []);
    expect(line?.stations.map((s) => s.name)).toEqual(["성신여대입구", "한성대입구", "혜화", "동대문"]);
  });

  it("returns null when the current station isn't part of the leg", () => {
    expect(buildBoardingLine(stations, "존재하지않음", [], [])).toBeNull();
  });

  it("drops non-direction-matched and unlocatable trains", () => {
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ matches_direction: false }), arriving({ stations_away: null })],
      [onboard({ station_index: 999 })],
    );
    expect(line?.trains).toEqual([]);
  });

  it("places an approaching arriving-train a quarter before the station it's nearing", () => {
    // stations_away: 1 -> nearing 동대문(idx 3), approaching from 혜화(idx 2)
    const line = buildBoardingLine(
      stations,
      "동대문역사문화공원",
      [arriving({ train_no: "4045", stations_away: 1 })],
      [],
    );
    expect(line?.trains).toEqual([
      { key: "arriving-4045-0", trainNo: "4045", destination: "오이도행", isExpress: false, state: "approaching", fromGapIndex: 1 },
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
    );
    expect(line?.trains).toEqual([
      { key: "onboard-arrived-1-0", trainNo: "arrived-1", destination: "오이도행", isExpress: false, state: "arrived", atIndex: 2 },
      { key: "onboard-departed-1-1", trainNo: "departed-1", destination: "오이도행", isExpress: false, state: "departed", fromGapIndex: 1 },
      { key: "onboard-approaching-1-2", trainNo: "approaching-1", destination: "오이도행", isExpress: false, state: "approaching", fromGapIndex: 3 },
    ]);
  });
});
