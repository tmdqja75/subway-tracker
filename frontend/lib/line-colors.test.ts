import { describe, expect, it } from "vitest";

import { getLineColor, LINE_COLOR_FALLBACK, LINE_COLORS, shortLineLabel } from "./line-colors";

describe("getLineColor", () => {
  it("resolves exact line_key values used by legs", () => {
    expect(getLineColor("2호선")).toBe(LINE_COLORS["2호선"]);
    expect(getLineColor("GTX-A")).toBe(LINE_COLORS["GTX-A"]);
    expect(getLineColor("경의중앙선")).toBe(LINE_COLORS["경의중앙선"]);
  });

  it("normalizes station-registry suffixes and formatting", () => {
    expect(getLineColor("9호선(연장)")).toBe(LINE_COLORS["9호선"]);
    expect(getLineColor("7호선(인천)")).toBe(LINE_COLORS["7호선"]);
    expect(getLineColor("신분당선(연장2)")).toBe(LINE_COLORS["신분당선"]);
    expect(getLineColor("공항철도1호선")).toBe(LINE_COLORS["공항철도"]);
    expect(getLineColor("수도권 광역급행철도")).toBe(LINE_COLORS["GTX-A"]);
  });

  it("maps legacy Korail corridor names to their branded line", () => {
    expect(getLineColor("분당선")).toBe(LINE_COLORS["수인분당선"]);
    expect(getLineColor("수인선")).toBe(LINE_COLORS["수인분당선"]);
    expect(getLineColor("경인선")).toBe(LINE_COLORS["1호선"]);
    expect(getLineColor("경부선")).toBe(LINE_COLORS["1호선"]);
    expect(getLineColor("경원선")).toBe(LINE_COLORS["1호선"]);
    expect(getLineColor("장항선")).toBe(LINE_COLORS["1호선"]);
    expect(getLineColor("일산선")).toBe(LINE_COLORS["3호선"]);
    expect(getLineColor("안산선")).toBe(LINE_COLORS["4호선"]);
    expect(getLineColor("과천선")).toBe(LINE_COLORS["4호선"]);
    expect(getLineColor("진접선")).toBe(LINE_COLORS["4호선"]);
    expect(getLineColor("별내선")).toBe(LINE_COLORS["8호선"]);
  });

  it("falls back to the neutral color for null and unresearched systems", () => {
    expect(getLineColor(null)).toBe(LINE_COLOR_FALLBACK);
    expect(getLineColor("인천1호선")).toBe(LINE_COLOR_FALLBACK);
    expect(getLineColor("김포골드라인")).toBe(LINE_COLOR_FALLBACK);
  });
});

describe("shortLineLabel", () => {
  it("abbreviates numbered and named lines", () => {
    expect(shortLineLabel("2호선")).toBe("2");
    expect(shortLineLabel("9호선(연장)")).toBe("9");
    expect(shortLineLabel("GTX-A")).toBe("GTX");
    expect(shortLineLabel("경인선")).toBe("1");
    expect(shortLineLabel("분당선")).toBe("수인");
    expect(shortLineLabel(null)).toBe("?");
  });
});
