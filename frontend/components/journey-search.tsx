"use client";

import { useEffect, useRef, useState } from "react";

import { getRouteHistory, searchRoutes } from "../lib/api";
import type { Itinerary, RouteHistoryItem, RouteHistoryResponse, Station } from "../lib/types";
import { Button } from "./ui/button";
import { LineBadge } from "./ui/line-badge";
import { StationAutocomplete } from "./station-autocomplete";

type JourneySearchProps = {
  onRoutes: (itineraries: Itinerary[]) => void;
};

type FieldErrors = {
  origin?: string;
  destination?: string;
};

type RouteHistorySectionProps = {
  heading: "Most Used Route" | "Recent Route";
  items: RouteHistoryItem[];
  isLoading: boolean;
  failed: boolean;
  isSearchLoading: boolean;
  onRouteSelect: (route: RouteHistoryItem) => void;
};

function routeLabel({ start, end }: RouteHistoryItem): string {
  return `${start.name} (${start.line}) → ${end.name} (${end.line})`;
}

function RouteHistorySection({
  failed,
  heading,
  isLoading,
  isSearchLoading,
  items,
  onRouteSelect,
}: RouteHistorySectionProps) {
  const headingId = `${heading === "Most Used Route" ? "most-used" : "recent"}-route-history-title`;
  const visibleItems = items.slice(0, 5);

  return (
    <section aria-labelledby={headingId} className="route-history__section">
      <h3 className="route-history__heading" id={headingId}>{heading}</h3>
      <ul className="route-history__list">
        {isLoading ? <li className="route-history__message" role="status">불러오는 중이에요…</li> : null}
        {!isLoading && visibleItems.map((route) => (
          <li className="route-history__item" key={`${route.start.station_id}-${route.end.station_id}`}>
            <button
              aria-label={routeLabel(route)}
              className="route-history__button"
              disabled={isSearchLoading}
              onClick={() => onRouteSelect(route)}
              type="button"
            >
              <span aria-hidden="true" className="route-history__stations">
                <span className="route-history__station-row">
                  <LineBadge line={route.start.line} />
                  <span className="route-history__station">{route.start.name}</span>
                </span>
                <span className="route-history__station-row">
                  <span className="route-history__arrow">→</span>
                  <LineBadge line={route.end.line} />
                  <span className="route-history__station">{route.end.name}</span>
                </span>
              </span>
            </button>
          </li>
        ))}
        {!isLoading && visibleItems.length === 0 ? (
          <li className="route-history__message">
            {failed ? "경로 기록을 불러오지 못했어요." : "저장된 경로가 없어요."}
          </li>
        ) : null}
      </ul>
    </section>
  );
}

export function JourneySearch({ onRoutes }: JourneySearchProps) {
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [originStation, setOriginStation] = useState<Station | null>(null);
  const [destinationStation, setDestinationStation] = useState<Station | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [routeHistory, setRouteHistory] = useState<RouteHistoryResponse | null>(null);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [historyFailed, setHistoryFailed] = useState(false);
  const requestController = useRef<AbortController | null>(null);
  const historyController = useRef<AbortController | null>(null);

  useEffect(() => () => requestController.current?.abort(), []);

  useEffect(() => {
    historyController.current?.abort();
    const controller = new AbortController();
    historyController.current = controller;
    setIsHistoryLoading(true);
    setHistoryFailed(false);

    getRouteHistory(controller.signal)
      .then((history) => {
        if (controller.signal.aborted || historyController.current !== controller) {
          return;
        }
        setRouteHistory(history);
      })
      .catch(() => {
        if (controller.signal.aborted || historyController.current !== controller) {
          return;
        }
        setHistoryFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted && historyController.current === controller) {
          setIsHistoryLoading(false);
        }
      });

    return () => {
      controller.abort();
      if (historyController.current === controller) {
        historyController.current = null;
      }
    };
  }, []);

  const applyRouteHistory = ({ start, end }: RouteHistoryItem) => {
    setOrigin(start.name);
    setDestination(end.name);
    setOriginStation(start);
    setDestinationStation(end);
    setFieldErrors({});
    setRequestError(null);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const start = origin.trim();
    const end = destination.trim();
    const nextErrors: FieldErrors = {};

    if (!start) {
      nextErrors.origin = "출발역을 입력해 주세요.";
    }
    if (!end) {
      nextErrors.destination = "도착역을 입력해 주세요.";
    }
    if (Object.keys(nextErrors).length) {
      setFieldErrors(nextErrors);
      return;
    }

    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setFieldErrors({});
    setRequestError(null);
    setIsLoading(true);

    try {
      const itineraries = await searchRoutes(
        {
          start,
          end,
          ...(originStation ? { start_id: originStation.station_id } : {}),
          ...(destinationStation ? { end_id: destinationStation.station_id } : {}),
        },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        onRoutes(itineraries);
      }
    } catch {
      if (!controller.signal.aborted) {
        setRequestError("경로를 찾지 못했어요. 잠시 후 다시 시도해 주세요.");
      }
    } finally {
      if (requestController.current === controller && !controller.signal.aborted) {
        setIsLoading(false);
      }
    }
  };

  return (
    <form className="journey-search" noValidate onSubmit={submit}>
      <div className="journey-search__fields">
        <StationAutocomplete
          disabled={isLoading}
          id="origin-station"
          label="출발역"
          onStationSelect={(station) => {
            setOriginStation(station);
            setFieldErrors((errors) => ({ ...errors, origin: undefined }));
          }}
          onValueChange={(value) => {
            setOrigin(value);
            setFieldErrors((errors) => ({ ...errors, origin: undefined }));
          }}
          placeholder="출발역을 입력하세요"
          required
          selectedStation={originStation}
          validationError={fieldErrors.origin}
          value={origin}
        />

        <StationAutocomplete
          disabled={isLoading}
          id="destination-station"
          label="도착역"
          onStationSelect={(station) => {
            setDestinationStation(station);
            setFieldErrors((errors) => ({ ...errors, destination: undefined }));
          }}
          onValueChange={(value) => {
            setDestination(value);
            setFieldErrors((errors) => ({ ...errors, destination: undefined }));
          }}
          placeholder="도착역을 입력하세요"
          required
          selectedStation={destinationStation}
          validationError={fieldErrors.destination}
          value={destination}
        />
      </div>

      {requestError ? <p className="field-error" role="alert">{requestError}</p> : null}
      <Button disabled={isLoading} type="submit" variant="primary">
        {isLoading ? "경로를 찾는 중이에요…" : "경로 찾기"}
      </Button>

      <div className="route-history" aria-label="저장된 경로">
        <RouteHistorySection
          failed={historyFailed}
          heading="Most Used Route"
          isLoading={isHistoryLoading}
          isSearchLoading={isLoading}
          items={routeHistory?.most_used ?? []}
          onRouteSelect={applyRouteHistory}
        />
        <RouteHistorySection
          failed={historyFailed}
          heading="Recent Route"
          isLoading={isHistoryLoading}
          isSearchLoading={isLoading}
          items={routeHistory?.recent ?? []}
          onRouteSelect={applyRouteHistory}
        />
      </div>
    </form>
  );
}
