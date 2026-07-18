"use client";

import { useEffect, useRef, useState } from "react";

import { boardCurrentJourney, getCurrentArrivals } from "../lib/api";
import type { ActiveJourneySnapshot, ArrivingTrain, CurrentArrivalsResponse } from "../lib/types";
import { Button } from "./ui/button";

type AwaitingBoardJourney = Extract<ActiveJourneySnapshot, { state: "awaiting_board" }>;

type TrainPickerProps = {
  journey: AwaitingBoardJourney;
  onJourneyRefresh: () => void;
};

const ARRIVALS_POLL_DELAY_MS = 15_000;
const ARRIVALS_LOAD_ERROR = "도착 열차 정보를 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.";
const BOARD_ERROR = "탑승을 시작하지 못했어요. 열차 정보를 새로고침한 뒤 다시 시도해 주세요.";

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function arrivalDisplay(train: ArrivingTrain): string {
  if (train.stations_away === 0) {
    return "진입";
  }
  if (train.stations_away !== null && train.stations_away > 0) {
    return `${train.stations_away_estimated ? "약 " : ""}${train.stations_away}정거장 전`;
  }
  return train.arrival_msg;
}

/**
 * Lets a rider board only a train the backend has already marked as travelling
 * in the journey's direction. Arrival refreshes are sequential to avoid stale
 * provider data replacing newer results.
 */
export function TrainPicker({ journey, onJourneyRefresh }: TrainPickerProps) {
  const [arrivals, setArrivals] = useState<CurrentArrivalsResponse | null>(null);
  const [arrivalsLoading, setArrivalsLoading] = useState(journey.leg.covered);
  const [arrivalsError, setArrivalsError] = useState<string | null>(null);
  const [refreshIndex, setRefreshIndex] = useState(0);
  const [boarding, setBoarding] = useState(false);
  const [boardSucceeded, setBoardSucceeded] = useState(false);
  const [boardError, setBoardError] = useState<string | null>(null);
  const boardControllerRef = useRef<AbortController | null>(null);
  const boardingRef = useRef(false);

  useEffect(() => {
    if (!journey.leg.covered) {
      setArrivals(null);
      setArrivalsLoading(false);
      setArrivalsError(null);
      return;
    }

    let active = true;
    let controller: AbortController | null = null;
    let timeout: number | null = null;

    const loadArrivals = () => {
      controller = new AbortController();
      setArrivalsLoading(true);
      setArrivalsError(null);

      void getCurrentArrivals(controller.signal)
        .then((nextArrivals) => {
          if (!active || controller?.signal.aborted) {
            return;
          }
          setArrivals(nextArrivals);
        })
        .catch((error: unknown) => {
          if (!active || controller?.signal.aborted || isAbortError(error)) {
            return;
          }
          setArrivalsError(ARRIVALS_LOAD_ERROR);
        })
        .finally(() => {
          if (!active || controller?.signal.aborted) {
            return;
          }
          setArrivalsLoading(false);
          timeout = window.setTimeout(loadArrivals, ARRIVALS_POLL_DELAY_MS);
        });
    };

    loadArrivals();

    return () => {
      active = false;
      if (timeout !== null) {
        window.clearTimeout(timeout);
      }
      controller?.abort();
    };
  }, [journey.journey_id, journey.leg.covered, journey.leg_idx, refreshIndex]);

  useEffect(
    () => () => {
      boardControllerRef.current?.abort();
      boardControllerRef.current = null;
      boardingRef.current = false;
    },
    [],
  );

  useEffect(() => {
    boardControllerRef.current?.abort();
    boardControllerRef.current = null;
    boardingRef.current = false;
    setBoarding(false);
    setBoardSucceeded(false);
    setBoardError(null);
  }, [journey.journey_id, journey.leg_idx]);

  const board = async (trainNo: string | null) => {
    if (boardingRef.current || boardSucceeded) {
      return;
    }

    const controller = new AbortController();
    boardingRef.current = true;
    boardControllerRef.current = controller;
    setBoarding(true);
    setBoardError(null);

    let succeeded = false;
    try {
      await boardCurrentJourney(trainNo, controller.signal);
      if (boardControllerRef.current === controller && !controller.signal.aborted) {
        succeeded = true;
        setBoardSucceeded(true);
        onJourneyRefresh();
      }
    } catch (error: unknown) {
      if (boardControllerRef.current === controller && !controller.signal.aborted && !isAbortError(error)) {
        setBoardError(errorMessage(error, BOARD_ERROR));
      }
    } finally {
      if (boardControllerRef.current === controller && !controller.signal.aborted) {
        boardControllerRef.current = null;
        if (!succeeded) {
          boardingRef.current = false;
        }
        setBoarding(false);
      }
    }
  };

  const eligibleTrains = arrivals?.trains.filter((train) => train.matches_direction) ?? [];
  const boardingLocked = boarding || boardSucceeded;

  return (
    <div className="train-picker">
      <div className="train-picker__leg" role="status">
        <p className="eyebrow">BOARDING · {journey.leg_idx + 1} / {journey.leg_count}</p>
        <strong>{journey.leg.route}</strong>
        <p>{journey.leg.start} → {journey.leg.end}</p>
      </div>

      {journey.leg.covered ? (
        <>
          <div className="train-picker__toolbar">
            <p>진행 방향에 맞는 열차만 표시합니다.</p>
            <Button
              aria-label="열차 목록 새로고침"
              disabled={boardingLocked}
              onClick={() => setRefreshIndex((index) => index + 1)}
              variant="ghost"
            >
              새로고침
            </Button>
          </div>

          {arrivalsLoading && arrivals === null ? <p className="train-picker__message" role="status">도착 열차를 확인하는 중이에요.</p> : null}
          {arrivalsError ? <p className="field-error" role="alert">{arrivalsError}</p> : null}
          {!arrivalsLoading && arrivals !== null && eligibleTrains.length === 0 ? (
            <p className="train-picker__message" role="status">
              현재 진행 방향에 맞는 탑승 가능 열차가 없어요. 잠시 후 새로고침해 주세요.
            </p>
          ) : null}
          {eligibleTrains.length > 0 ? (
            <ol aria-label="탑승 가능한 열차" className="train-picker__trains">
              {eligibleTrains.map((train) => (
                <li key={train.train_no}>
                  <button
                    className="train-card"
                    disabled={boardingLocked}
                    onClick={() => void board(train.train_no)}
                    type="button"
                  >
                    <span className="train-card__header">
                      <span aria-level={3} className="train-card__number" role="heading">{train.train_no} 열차</span>
                      {train.is_express ? <span className="train-card__express">급행</span> : null}
                    </span>
                    <span className="train-card__direction">{train.direction_label}</span>
                    <span className="train-card__terminus">{train.terminus} 종착</span>
                    <span className="train-card__arrival">{arrivalDisplay(train)}</span>
                    <span className="train-card__action">
                      {boardSucceeded ? "탑승 확인 중이에요…" : boarding ? "탑승을 시작하는 중이에요…" : "이 열차 탑승"}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : null}
        </>
      ) : (
        <div className="train-picker__timer">
          <p>이 구간은 실시간 열차 위치를 지원하지 않아요.</p>
          <p>탑승을 시작하면 예정 이동 시간을 기준으로 여정을 안내합니다.</p>
          <Button disabled={boardingLocked} onClick={() => void board(null)}>
            {boardSucceeded ? "탑승 확인 중이에요…" : boarding ? "탑승을 시작하는 중이에요…" : "시간 기준으로 탑승 시작"}
          </Button>
        </div>
      )}

      {boardError ? <p className="field-error" role="alert">{boardError}</p> : null}
    </div>
  );
}
