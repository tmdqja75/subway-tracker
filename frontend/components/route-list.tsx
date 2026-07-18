"use client";

import { useEffect, useRef, useState } from "react";

import { startJourney } from "../lib/api";
import type { Itinerary } from "../lib/types";
import { Button } from "./ui/button";

type RouteListProps = {
  itineraries: Itinerary[];
  onBack: () => void;
  onStarted: () => void;
};

function minutes(seconds: number): string {
  return `${Math.ceil(seconds / 60)}분`;
}

function fareLabel(fare: number | null): string {
  return fare === null ? "요금 정보 없음" : `${fare.toLocaleString("ko-KR")}원`;
}

export function RouteList({ itineraries, onBack, onStarted }: RouteListProps) {
  const [startingIndex, setStartingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestController = useRef<AbortController | null>(null);

  useEffect(() => () => requestController.current?.abort(), []);

  const beginJourney = async (itinerary: Itinerary, index: number) => {
    if (startingIndex !== null) {
      return;
    }

    const controller = new AbortController();
    requestController.current = controller;
    setStartingIndex(index);
    setError(null);

    try {
      await startJourney({ itinerary }, controller.signal);
      if (!controller.signal.aborted) {
        onStarted();
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("여정을 시작하지 못했어요. 잠시 후 다시 시도해 주세요.");
      }
    } finally {
      if (requestController.current === controller && !controller.signal.aborted) {
        setStartingIndex(null);
      }
    }
  };

  if (!itineraries.length) {
    return (
      <div className="route-list route-list--empty">
        <p>검색 결과에 맞는 경로가 없어요. 역 이름을 다시 확인해 주세요.</p>
        <Button onClick={onBack} variant="secondary">출발역·도착역 다시 입력</Button>
      </div>
    );
  }

  return (
    <div className="route-list">
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      <ol aria-label="추천 경로" className="route-list__items">
        {itineraries.map((itinerary, index) => {
          const realtimeUnavailable = itinerary.legs.some((leg) => leg.line_key === null);
          const isStarting = startingIndex === index;

          return (
            <li className="route-list__item" key={`${itinerary.total_time}-${index}`}>
              <button
                className="route-card"
                disabled={startingIndex !== null}
                onClick={() => beginJourney(itinerary, index)}
                type="button"
              >
                <span className="route-card__heading">
                  <span className="eyebrow">ROUTE {index + 1}</span>
                  <span aria-level={3} className="route-card__title" role="heading">소요 {minutes(itinerary.total_time)}</span>
                </span>
                <span className="route-card__metrics">
                  <span className="route-card__metric">
                    <span className="route-card__metric-label">환승</span>
                    <span className="route-card__metric-value">환승 {itinerary.transfer_count}회</span>
                  </span>
                  <span className="route-card__metric">
                    <span className="route-card__metric-label">도보</span>
                    <span className="route-card__metric-value">도보 {minutes(itinerary.total_walk_time)}</span>
                  </span>
                  <span className="route-card__metric">
                    <span className="route-card__metric-label">요금</span>
                    <span className="route-card__metric-value">{fareLabel(itinerary.fare)}</span>
                  </span>
                </span>
                <span className="route-card__summary">{itinerary.summary.join(" · ")}</span>
                <span className="route-card__coverage">
                  {realtimeUnavailable
                    ? "실시간 안내 미지원 구간이 있어 시간 기준으로 안내됩니다."
                    : "실시간 안내가 가능한 구간은 탑승 후 현재 흐름으로 안내됩니다."}
                </span>
                <span className="route-card__action">
                  {isStarting ? "여정을 시작하는 중이에요…" : "이 경로로 시작"}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <Button disabled={startingIndex !== null} onClick={onBack} variant="ghost">출발역·도착역 다시 입력</Button>
    </div>
  );
}
