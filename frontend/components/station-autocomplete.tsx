"use client";

import { useEffect, useId, useRef, useState } from "react";

import { searchStations } from "../lib/api";
import type { Station } from "../lib/types";
import { LineBadge } from "./ui/line-badge";

type StationAutocompleteProps = {
  id: string;
  label: string;
  placeholder: string;
  value: string;
  selectedStation: Station | null;
  onValueChange: (value: string) => void;
  onStationSelect: (station: Station | null) => void;
  disabled?: boolean;
  required?: boolean;
  validationError?: string;
};

export function StationAutocomplete({
  disabled = false,
  id,
  label,
  onStationSelect,
  onValueChange,
  placeholder,
  required = false,
  selectedStation,
  validationError,
  value,
}: StationAutocompleteProps) {
  const generatedId = useId().replace(/:/g, "");
  const listboxId = `${id}-listbox`;
  const errorId = `${id}-error-${generatedId}`;
  const selectedStationId = `${id}-selected-${generatedId}`;
  const validationErrorId = `${id}-validation-error-${generatedId}`;
  const [stations, setStations] = useState<Station[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [error, setError] = useState<string | null>(null);
  const descriptionIds = [
    selectedStation ? selectedStationId : null,
    error ? errorId : null,
    validationError ? validationErrorId : null,
  ].filter(Boolean).join(" ") || undefined;
  const requestSequence = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const sequence = ++requestSequence.current;
    const query = value.trim();

    setActiveIndex(-1);
    setError(null);
    setIsLoading(false);

    if (!query || selectedStation?.name === value) {
      setStations([]);
      setIsOpen(false);
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setIsLoading(true);
      searchStations(query, controller.signal)
        .then((nextStations) => {
          if (controller.signal.aborted || requestSequence.current !== sequence) {
            return;
          }
          setStations(nextStations);
          setIsOpen(true);
        })
        .catch(() => {
          if (controller.signal.aborted || requestSequence.current !== sequence) {
            return;
          }
          setStations([]);
          setError("역을 찾지 못했어요.");
          setIsOpen(true);
        })
        .finally(() => {
          if (!controller.signal.aborted && requestSequence.current === sequence) {
            setIsLoading(false);
          }
        });
    }, 250);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [selectedStation, value]);

  const selectStation = (station: Station) => {
    onValueChange(station.name);
    onStationSelect(station);
    setStations([]);
    setIsOpen(false);
    setActiveIndex(-1);
  };

  const reselectStation = () => {
    onStationSelect(null);
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      setIsOpen(false);
      setActiveIndex(-1);
      return;
    }

    if (!stations.length) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setIsOpen(true);
      setActiveIndex((index) => (index + 1) % stations.length);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setIsOpen(true);
      setActiveIndex((index) => (index <= 0 ? stations.length - 1 : index - 1));
      return;
    }

    if (event.key === "Enter" && isOpen && activeIndex >= 0) {
      event.preventDefault();
      selectStation(stations[activeIndex]);
    }
  };

  const showEmpty = isOpen && !isLoading && !error && value.trim().length > 0 && stations.length === 0;

  return (
    <div className="station-autocomplete">
      <label className="station-autocomplete__label" htmlFor={id}>
        {label}
      </label>
      <input
        aria-activedescendant={activeIndex >= 0 && isOpen ? `${id}-option-${activeIndex}` : undefined}
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-describedby={descriptionIds}
        aria-expanded={isOpen}
        aria-invalid={validationError ? true : undefined}
        autoComplete="off"
        className="station-autocomplete__input"
        disabled={disabled}
        id={id}
        onBlur={() => setIsOpen(false)}
        onChange={(event) => {
          const nextValue = event.target.value;
          if (selectedStation && nextValue !== selectedStation.name) {
            onStationSelect(null);
          }
          onValueChange(nextValue);
        }}
        onFocus={() => {
          if (stations.length || isLoading || error) {
            setIsOpen(true);
          }
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        required={required}
        ref={inputRef}
        role="combobox"
        value={value}
      />
      {selectedStation ? (
        <div className="station-autocomplete__selected">
          <p className="station-autocomplete__selection" id={selectedStationId} role="status">
            <LineBadge line={selectedStation.line} /> 선택됨: {selectedStation.name} · {selectedStation.line}
          </p>
          <button className="station-autocomplete__reselect" onClick={reselectStation} type="button">
            다른 호선 선택
          </button>
        </div>
      ) : null}
      {isLoading ? <p className="station-autocomplete__message">역을 찾는 중이에요…</p> : null}
      {error ? (
        <p className="station-autocomplete__message station-autocomplete__message--error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
      {validationError ? <p className="field-error" id={validationErrorId} role="alert">{validationError}</p> : null}
      {isOpen && stations.length ? (
        <ul aria-label={`${label} 검색 결과`} className="station-autocomplete__list" id={listboxId} role="listbox">
          {stations.map((station, index) => (
            <li
              aria-label={`${station.name} ${station.line}`}
              aria-selected={index === activeIndex}
              className="station-autocomplete__option"
              id={`${id}-option-${index}`}
              key={station.station_id}
              onClick={() => selectStation(station)}
              onMouseDown={(event) => event.preventDefault()}
              onPointerDown={(event) => event.preventDefault()}
              role="option"
            >
              <span className="station-autocomplete__option-name">
                <LineBadge line={station.line} />
                <strong>{station.name}</strong>
              </span>
              <span>{station.line}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {showEmpty ? <p className="station-autocomplete__message">일치하는 역이 없어요.</p> : null}
    </div>
  );
}
