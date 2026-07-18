import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  alightCurrentJourney,
  boardCurrentJourney,
  cancelCurrentJourney,
  getCurrentArrivals,
  getCurrentJourney,
  getCurrentJourneyPoints,
  markCurrentJourneyMissed,
  retryCurrentJourneyPush,
  searchRoutes,
  searchStations,
  startJourney,
} from "./api";
import type {
  ApiErrorResponse,
  BoardJourneyRequest,
  JourneyStartedResponse,
  RouteSearchRequest,
  StartJourneyRequest,
} from "./types";
import { itinerary, station } from "../test/fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("rider API client", () => {
  it("returns typed success JSON and encodes station-search queries", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([station]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchStations("강남 역&2호선")).resolves.toEqual([station]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/stations/search?q=%EA%B0%95%EB%82%A8%20%EC%97%AD%262%ED%98%B8%EC%84%A0",
      expect.objectContaining({ cache: "no-store", method: "GET" }),
    );
  });

  it("sends JSON bodies and content type for write endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([itinerary]))
      .mockResolvedValueOnce(jsonResponse([itinerary]));
    vi.stubGlobal("fetch", fetchMock);
    const nullableStationIds: RouteSearchRequest = {
      start: "강남",
      end: "역삼",
      start_id: null,
      end_id: null,
    };
    const omittedStationIds: RouteSearchRequest = { start: "강남", end: "역삼" };

    await expect(searchRoutes(nullableStationIds)).resolves.toEqual([itinerary]);
    await expect(searchRoutes(omittedStationIds)).resolves.toEqual([itinerary]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/routes",
      expect.objectContaining({
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: "강남",
          end: "역삼",
          start_id: null,
          end_id: null,
        }),
      }),
    );
  });

  it("extracts backend detail text into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "station not found: 없는역" }, 404)),
    );

    await expect(searchRoutes({ start: "없는역", end: "강남" })).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "station not found: 없는역",
    });
  });

  it("uses the Korean network error message when fetch cannot connect", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));

    await expect(getCurrentJourney()).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        message:
          `서버(${window.location.origin})에 연결할 수 없어요. ` +
          "uvicorn이 실행 중인지, 주소/포트가 맞는지 확인하세요.",
      }),
    );
  });

  it("preserves AbortError rejections instead of converting them to ApiError", async () => {
    const abortError = new DOMException("The request was aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));

    await expect(getCurrentJourney()).rejects.toBe(abortError);
  });

  it("preserves an AbortSignal reason when the request is cancelled", async () => {
    const controller = new AbortController();
    const reason = new Error("poll superseded");
    controller.abort(reason);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));

    await expect(getCurrentJourney(controller.signal)).rejects.toBe(reason);
  });

  it("preserves cancellation when a non-OK response body rejects during parsing", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("The request was aborted", "AbortError");
    controller.abort(abortError);
    const response = {
      ok: false,
      status: 503,
      json: vi.fn().mockRejectedValue(abortError),
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getCurrentJourney(controller.signal)).rejects.toBe(abortError);
    expect(response.json).toHaveBeenCalledOnce();
  });

  it("rethrows AbortError when a non-OK response body parsing is cancelled", async () => {
    const abortError = new DOMException("The request was aborted", "AbortError");
    const response = {
      ok: false,
      status: 503,
      json: vi.fn().mockRejectedValue(abortError),
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getCurrentJourney()).rejects.toBe(abortError);
    expect(response.json).toHaveBeenCalledOnce();
  });

  it("forwards AbortSignal while keeping reads uncached", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ state: "idle" }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(getCurrentJourney(controller.signal)).resolves.toEqual({ state: "idle" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/journeys/current",
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        signal: controller.signal,
      }),
    );
  });

  it("calls every rider endpoint with its complete RequestInit contract", async () => {
    const startedResponse = {
      journey_id: 1,
      state: "awaiting_board",
    } satisfies JourneyStartedResponse;
    const routeRequest: RouteSearchRequest = {
      start: "강남",
      end: "역삼",
      start_id: null,
      end_id: null,
    };
    const startRequest: StartJourneyRequest = { itinerary };
    const requestWithoutTrain: BoardJourneyRequest = {};
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string) => jsonResponse(path === "/api/journeys" ? startedResponse : { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const cases: Array<{
      path: string;
      call: () => Promise<unknown>;
      method: "GET" | "POST";
      body?: unknown;
    }> = [
      {
        path: "/api/stations/search?q=%EA%B0%95%EB%82%A8%20%EC%97%AD%262%ED%98%B8%EC%84%A0",
        call: () => searchStations("강남 역&2호선"),
        method: "GET",
      },
      { path: "/api/routes", call: () => searchRoutes(routeRequest), method: "POST", body: routeRequest },
      { path: "/api/journeys", call: () => startJourney(startRequest), method: "POST", body: startRequest },
      { path: "/api/journeys/current", call: () => getCurrentJourney(), method: "GET" },
      { path: "/api/journeys/current/arrivals", call: () => getCurrentArrivals(), method: "GET" },
      {
        path: "/api/journeys/current/board",
        call: () => boardCurrentJourney(null),
        method: "POST",
        body: { train_no: null },
      },
      { path: "/api/journeys/current/alight", call: () => alightCurrentJourney(), method: "POST" },
      { path: "/api/journeys/current/missed", call: () => markCurrentJourneyMissed(), method: "POST" },
      { path: "/api/journeys/current/cancel", call: () => cancelCurrentJourney(), method: "POST" },
      {
        path: "/api/journeys/current/retry-push",
        call: () => retryCurrentJourneyPush(),
        method: "POST",
      },
      { path: "/api/journeys/current/points", call: () => getCurrentJourneyPoints(), method: "GET" },
    ];

    for (const endpoint of cases) {
      await endpoint.call();
    }

    expect(fetchMock.mock.calls).toHaveLength(cases.length);
    cases.forEach((endpoint, index) => {
      const expectedInit = {
        method: endpoint.method,
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: undefined,
        ...(endpoint.body === undefined ? {} : { body: JSON.stringify(endpoint.body) }),
      };
      expect(fetchMock.mock.calls[index]).toEqual([endpoint.path, expectedInit]);
    });
    expect(requestWithoutTrain).toEqual({});
  });

  it("models both text and FastAPI validation-list error details", () => {
    const textError: ApiErrorResponse = { detail: "station not found" };
    const validationError: ApiErrorResponse = {
      detail: [
        {
          type: "missing",
          loc: ["body", "start"],
          msg: "Field required",
          input: {},
        },
      ],
    };

    expect(textError.detail).toBe("station not found");
    expect(validationError.detail).toHaveLength(1);
  });

  it("exports ApiError as the safe client error type", () => {
    expect(new ApiError("failure", 500)).toBeInstanceOf(Error);
  });
});
