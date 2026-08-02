import type {
  ActiveJourneySnapshot,
  ArrivingTrain,
  CurrentJourneyResponse,
  Itinerary,
  RouteHistoryResponse,
  Station,
} from "../lib/types";
import type { Page, Route } from "@playwright/test";

export const originStation: Station = {
  station_id: "0222",
  name: "강남",
  line: "2호선",
  lat: 37.4979,
  lon: 127.0276,
};

export const destinationStation: Station = {
  station_id: "0214",
  name: "홍대입구",
  line: "2호선",
  lat: 37.5571,
  lon: 126.9245,
};

const legStations = [
  { index: 0, name: "강남", lat: 37.4979, lon: 127.0276 },
  { index: 1, name: "역삼", lat: 37.5006, lon: 127.0364 },
];

const completedTrip = {
  legs: [
    {
      route: "수도권2호선",
      start: "강남",
      end: "홍대입구",
      stations: legStations,
      shape: [],
      transfer_walk_shape: [],
    },
  ],
};

const stationSearchResults: Record<string, Station[]> = {
  "강남": [originStation],
  "홍대": [destinationStation],
};

export const routeHistory: RouteHistoryResponse = {
  most_used: [{ start: originStation, end: destinationStation }],
  recent: [{ start: destinationStation, end: originStation }],
};

const transparentPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mNk+M/wHwAF/gL+0HhdnQAAAABJRU5ErkJggg==",
  "base64",
);

export const itineraries: Itinerary[] = [
  {
    total_time: 1_500,
    transfer_count: 0,
    total_walk_time: 120,
    fare: 1_400,
    summary: ["강남에서 2호선 탑승", "홍대입구 하차"],
    legs: [
      {
        route: "수도권2호선",
        line_key: "2",
        section_time: 1_380,
        start_name: "강남",
        end_name: "홍대입구",
        stations: legStations,
        shape: [],
        transfer_walk_shape: [],
        transfer_walk_time: 120,
      },
    ],
  },
  {
    total_time: 1_680,
    transfer_count: 1,
    total_walk_time: 300,
    fare: 1_400,
    summary: ["강남에서 9호선 탑승", "환승 후 홍대입구 하차"],
    legs: [
      {
        route: "수도권9호선",
        line_key: "9",
        section_time: 1_380,
        start_name: "강남",
        end_name: "홍대입구",
        stations: legStations,
        shape: [],
        transfer_walk_shape: [],
        transfer_walk_time: 300,
      },
    ],
  },
];

function baseJourney() {
  return {
    journey_id: 42,
    leg_idx: 0,
    leg_count: 1,
    leg: {
      route: "수도권2호선",
      line_key: "2",
      start: "강남",
      end: "홍대입구",
      covered: true,
      stations: legStations,
      shape: [],
      transfer_walk_shape: [],
      transfer_walk_time: 0,
    },
    summary: itineraries[0].summary,
    train: null,
    tracking_mode: null,
    history_estimated: false,
    point_count: 0,
    error: null,
    trip: completedTrip,
  };
}

export function awaitingBoardJourney(): Extract<ActiveJourneySnapshot, { state: "awaiting_board" }> {
  return { ...baseJourney(), state: "awaiting_board", transfer: null };
}

export function timerAwaitingBoardJourney(): Extract<ActiveJourneySnapshot, { state: "awaiting_board" }> {
  return {
    ...baseJourney(),
    state: "awaiting_board",
    leg: { ...baseJourney().leg, route: "공항철도", line_key: null, covered: false },
    transfer: null,
  };
}

export function activeOnTrainJourney(
  mode: "realtime" | "timer" = "realtime",
): Extract<ActiveJourneySnapshot, { state: "on_train" }> {
  return {
    ...baseJourney(),
    state: "on_train",
    tracking_mode: mode,
    train: mode === "realtime"
      ? {
          train_no: "2207",
          station_name: "역삼",
          station_index: 1,
          status: "운행 중",
          lat: 37.5006,
          lon: 127.0364,
          updated_at: 1_783_000_000,
        }
      : null,
    point_count: mode === "realtime" ? 12 : 4,
    transfer: null,
  };
}

export function pushingJourney(): Extract<ActiveJourneySnapshot, { state: "pushing" }> {
  return {
    ...baseJourney(),
    state: "pushing",
    transfer: { sent_points: 2, total_points: 5, remaining_points: 3, progress_percent: 40 },
  };
}

export function completedJourney(): Extract<ActiveJourneySnapshot, { state: "completed" }> {
  return {
    ...baseJourney(),
    state: "completed",
    transfer: { sent_points: 5, total_points: 5, remaining_points: 0, progress_percent: 100 },
  };
}

export function pushFailedJourney(): Extract<ActiveJourneySnapshot, { state: "push_failed" }> {
  return {
    ...baseJourney(),
    state: "push_failed",
    error: "upstream timed out",
    transfer: {
      sent_points: 3,
      total_points: 5,
      remaining_points: 2,
      progress_percent: 60,
      reason: "network",
      message: "전송 중 네트워크 오류가 발생했어요.",
      detail: "upstream timed out",
      can_retry: true,
    },
  };
}

const arrivals: ArrivingTrain[] = [
  {
    train_no: "2207",
    line_name: "2호선",
    terminus: "성수",
    direction_label: "성수행 - 구의방면",
    eta_seconds: 40,
    arrival_msg: "곧 도착",
    status: "approaching",
    stations_away: 0,
    stations_away_estimated: false,
    matches_direction: true,
    is_express: false,
  },
  {
    train_no: "2208",
    line_name: "2호선",
    terminus: "신도림",
    direction_label: "신도림행 - 반대 방향",
    eta_seconds: 70,
    arrival_msg: "1분 후 도착",
    status: "approaching",
    stations_away: 1,
    stations_away_estimated: false,
    matches_direction: false,
    is_express: false,
  },
];

export type MockApiState = {
  arrivalsRequests: number;
  boardRequests: Array<{ train_no: string | null; retroactive: boolean }>;
  current: CurrentJourneyResponse;
  fulfilledOpenStreetMapTileRequests: Array<{ hostname: string; method: string; url: string }>;
  itineraries: Itinerary[];
  retryMethods: string[];
  retryPayloads: Array<string | null>;
  retryRequests: number;
  routeHistoryRequests: number;
  routeRequests: unknown[];
  stationSearchRequests: Array<{ method: string; q: string }>;
  startRequests: unknown[];
};

export function createMockApiState({ current }: { current: CurrentJourneyResponse }): MockApiState {
  return {
    arrivalsRequests: 0,
    boardRequests: [],
    current,
    fulfilledOpenStreetMapTileRequests: [],
    itineraries,
    retryMethods: [],
    retryPayloads: [],
    retryRequests: 0,
    routeHistoryRequests: 0,
    routeRequests: [],
    stationSearchRequests: [],
    startRequests: [],
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({ contentType: "application/json", body: JSON.stringify(body), status: 200 });
}

/** Mocks the production /api contract and isolates map image requests for deterministic browser tests. */
export async function installMockBackend(page: Page, state: MockApiState) {
  const fulfillOpenStreetMapTile = async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    await route.fulfill({
      body: transparentPng,
      contentType: "image/png",
      headers: { "cache-control": "no-store" },
      status: 200,
    });
    state.fulfilledOpenStreetMapTileRequests.push({
      hostname: url.hostname,
      method: request.method(),
      url: request.url(),
    });
  };

  await page.route("https://*.tile.openstreetmap.org/**", fulfillOpenStreetMapTile);
  await page.route("https://tile.openstreetmap.org/**", fulfillOpenStreetMapTile);
  await page.route("**/marker-*.png", async (route) => {
    await route.fulfill({ body: transparentPng, contentType: "image/png", status: 200 });
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;

    if (request.method() === "GET" && pathname === "/api/journeys/current") {
      await json(route, state.current);
      return;
    }
    if (request.method() === "GET" && pathname === "/api/stations/search") {
      const query = url.searchParams.get("q") ?? "";
      state.stationSearchRequests.push({ method: request.method(), q: query });
      const results = stationSearchResults[query];
      if (!results) {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ detail: `Unexpected station search query: ${query}` }),
          status: 400,
        });
        return;
      }
      await json(route, results);
      return;
    }
    if (request.method() === "GET" && pathname === "/api/routes/history") {
      state.routeHistoryRequests += 1;
      await json(route, routeHistory);
      return;
    }
    if (request.method() === "POST" && pathname === "/api/routes") {
      state.routeRequests.push(request.postDataJSON());
      await json(route, state.itineraries);
      return;
    }
    if (request.method() === "POST" && pathname === "/api/journeys") {
      state.startRequests.push(request.postDataJSON());
      state.current = awaitingBoardJourney();
      await json(route, { journey_id: 42, state: "awaiting_board" });
      return;
    }
    if (request.method() === "GET" && pathname === "/api/journeys/current/arrivals") {
      state.arrivalsRequests += 1;
      await json(route, { covered: true, trains: arrivals, already_onboard: [], context_before: ["교대"] });
      return;
    }
    if (request.method() === "POST" && pathname === "/api/journeys/current/board") {
      state.boardRequests.push(request.postDataJSON());
      state.current = activeOnTrainJourney(state.current.state === "awaiting_board" && !state.current.leg.covered ? "timer" : "realtime");
      await json(route, { ok: true });
      return;
    }
    if (request.method() === "POST" && pathname === "/api/journeys/current/retry-push") {
      state.retryRequests += 1;
      state.retryMethods.push(request.method());
      state.retryPayloads.push(request.postData());
      state.current = completedJourney();
      await json(route, { ok: true });
      return;
    }
    if (request.method() === "GET" && pathname === "/api/journeys/current/points") {
      await json(route, []);
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ detail: `Unhandled mocked endpoint: ${request.method()} ${pathname}` }),
      status: 500,
    });
  });
}
