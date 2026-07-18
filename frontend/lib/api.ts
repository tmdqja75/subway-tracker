import type {
  BoardJourneyRequest,
  CurrentArrivalsResponse,
  CurrentJourneyResponse,
  Itinerary,
  JourneyStartedResponse,
  OkResponse,
  RouteSearchRequest,
  StartJourneyRequest,
  Station,
  TrackPoint,
} from "./types";

function networkErrorMessage(): string {
  const server = typeof location === "undefined" ? "서버" : `서버(${location.origin})`;
  return `${server}에 연결할 수 없어요. uvicorn이 실행 중인지, 주소/포트가 맞는지 확인하세요.`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
}

function isDetailResponse(value: unknown): value is { detail: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "detail" in value &&
    typeof value.detail === "string"
  );
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/** Performs a same-origin JSON request using the backend's canonical /api paths. */
export async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  let response: Response;

  try {
    response = await fetch(path, {
      method: options.method,
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      signal: options.signal,
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
    });
  } catch (error: unknown) {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? error;
    }
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError(networkErrorMessage());
  }

  if (!response.ok) {
    let errorBody: unknown;
    try {
      errorBody = await response.json();
    } catch (error: unknown) {
      if (options.signal?.aborted) {
        throw options.signal.reason ?? error;
      }
      if (isAbortError(error)) {
        throw error;
      }
      errorBody = undefined;
    }
    throw new ApiError(
      isDetailResponse(errorBody) ? errorBody.detail : `HTTP ${response.status}`,
      response.status,
    );
  }

  return response.json() as Promise<T>;
}

export function searchStations(query: string, signal?: AbortSignal): Promise<Station[]> {
  return requestJson<Station[]>(`/api/stations/search?q=${encodeURIComponent(query)}`, {
    method: "GET",
    signal,
  });
}

export function searchRoutes(
  request: RouteSearchRequest,
  signal?: AbortSignal,
): Promise<Itinerary[]> {
  return requestJson<Itinerary[]>("/api/routes", { method: "POST", body: request, signal });
}

export function startJourney(
  request: StartJourneyRequest,
  signal?: AbortSignal,
): Promise<JourneyStartedResponse> {
  return requestJson<JourneyStartedResponse>("/api/journeys", {
    method: "POST",
    body: request,
    signal,
  });
}

export function getCurrentJourney(signal?: AbortSignal): Promise<CurrentJourneyResponse> {
  return requestJson<CurrentJourneyResponse>("/api/journeys/current", { method: "GET", signal });
}

export function getCurrentArrivals(signal?: AbortSignal): Promise<CurrentArrivalsResponse> {
  return requestJson<CurrentArrivalsResponse>("/api/journeys/current/arrivals", {
    method: "GET",
    signal,
  });
}

export function boardCurrentJourney(
  trainNo: BoardJourneyRequest["train_no"],
  signal?: AbortSignal,
): Promise<OkResponse> {
  return requestJson<OkResponse>("/api/journeys/current/board", {
    method: "POST",
    body: { train_no: trainNo },
    signal,
  });
}

export function alightCurrentJourney(signal?: AbortSignal): Promise<OkResponse> {
  return requestJson<OkResponse>("/api/journeys/current/alight", { method: "POST", signal });
}

export function markCurrentJourneyMissed(signal?: AbortSignal): Promise<OkResponse> {
  return requestJson<OkResponse>("/api/journeys/current/missed", { method: "POST", signal });
}

export function cancelCurrentJourney(signal?: AbortSignal): Promise<OkResponse> {
  return requestJson<OkResponse>("/api/journeys/current/cancel", { method: "POST", signal });
}

export function retryCurrentJourneyPush(signal?: AbortSignal): Promise<OkResponse> {
  return requestJson<OkResponse>("/api/journeys/current/retry-push", {
    method: "POST",
    signal,
  });
}

export function getCurrentJourneyPoints(signal?: AbortSignal): Promise<TrackPoint[]> {
  return requestJson<TrackPoint[]>("/api/journeys/current/points", { method: "GET", signal });
}
