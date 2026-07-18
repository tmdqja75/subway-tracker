"use client";

import { useEffect, useRef, useState } from "react";

import { searchRoutes } from "../lib/api";
import type { Itinerary, Station } from "../lib/types";
import { Button } from "./ui/button";
import { StationAutocomplete } from "./station-autocomplete";

type JourneySearchProps = {
  onRoutes: (itineraries: Itinerary[]) => void;
};

type FieldErrors = {
  origin?: string;
  destination?: string;
};

export function JourneySearch({ onRoutes }: JourneySearchProps) {
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [originStation, setOriginStation] = useState<Station | null>(null);
  const [destinationStation, setDestinationStation] = useState<Station | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => () => requestController.current?.abort(), []);

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
    </form>
  );
}
