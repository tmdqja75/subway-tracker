/**
 * Official line colors from Korail's railway signage manual (2017 공공시설물
 * 표준디자인 개발 ver.2, p.59 노선색), matching `app/lines.py`'s line_key set.
 */
export const LINE_COLORS: Record<string, string> = {
  "1호선": "#0052A4",
  "2호선": "#00A84D",
  "3호선": "#EF7C1C",
  "4호선": "#00A4E3",
  "5호선": "#996CAC",
  "6호선": "#CD7C2F",
  "7호선": "#747F00",
  "8호선": "#E6186C",
  "9호선": "#BDB092",
  "GTX-A": "#9A6292",
  "중앙선": "#77C4A3",
  "경의중앙선": "#77C4A3",
  "수인분당선": "#F5A200",
  "신분당선": "#D4003B",
  "경춘선": "#0C8E72",
  "경강선": "#003DA5",
  "우이신설선": "#B0CE18",
  "서해선": "#81A914",
  "공항철도": "#0090D2",
};

/** Neutral ink used when a line has no covered/researched color (walking transfer, unsupported line). */
export const LINE_COLOR_FALLBACK = "#5B6570";

/**
 * data/stations.csv's 호선 column mixes the branded line_key with the older
 * Korail corridor names for segments that were folded into a branded line
 * (경인선/경부선/경원선/장항선 -> 1호선, 일산선 -> 3호선, 안산선/과천선 -> 4호선,
 * 진접선 -> 4호선 연장, 별내선 -> 8호선 연장, 분당선/수인선 -> 수인분당선).
 * Non-numbered corridor names were falling through unmatched, which is why
 * only numbered lines were getting colored before this map existed.
 */
const SEGMENT_ALIASES: Record<string, string> = {
  "경인선": "1호선",
  "경부선": "1호선",
  "경원선": "1호선",
  "장항선": "1호선",
  "일산선": "3호선",
  "안산선": "4호선",
  "과천선": "4호선",
  "진접선": "4호선",
  "별내선": "8호선",
  "분당선": "수인분당선",
  "수인선": "수인분당선",
};

/**
 * Station registry line labels also carry suffixes and formatting the
 * backend's line_key set doesn't ("9호선(연장)", "공항철도1호선", "수도권
 * 광역급행철도") — this maps those down to a LINE_COLORS key. Systems the
 * registry covers but this codebase has no researched color for (인천
 * 1/2호선, 의정부경전철, 김포골드라인, 신림선, 에버라인선) fall through to the
 * neutral fallback rather than guessing a color.
 */
function normalizeLineLabel(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed in LINE_COLORS) {
    return trimmed;
  }
  if (trimmed in SEGMENT_ALIASES) {
    return SEGMENT_ALIASES[trimmed];
  }
  const numbered = trimmed.match(/^(\d+)호선/);
  if (numbered) {
    return `${numbered[1]}호선`;
  }
  if (trimmed.startsWith("수도권") && trimmed.includes("광역급행철도")) {
    return "GTX-A";
  }
  if (trimmed.startsWith("공항철도")) {
    return "공항철도";
  }
  if (trimmed.startsWith("신분당선")) {
    return "신분당선";
  }
  return trimmed;
}

export function getLineColor(lineKey: string | null | undefined): string {
  if (!lineKey) {
    return LINE_COLOR_FALLBACK;
  }
  return LINE_COLORS[normalizeLineLabel(lineKey)] ?? LINE_COLOR_FALLBACK;
}

/** Short badge text: "2호선" -> "2", "GTX-A" -> "GTX", others -> first two characters. */
export function shortLineLabel(lineKey: string | null | undefined): string {
  if (!lineKey) {
    return "?";
  }
  const normalized = normalizeLineLabel(lineKey);
  const numbered = normalized.match(/^(\d+)호선$/);
  if (numbered) {
    return numbered[1];
  }
  if (normalized === "GTX-A") {
    return "GTX";
  }
  return normalized.slice(0, 2);
}
