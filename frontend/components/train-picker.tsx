"use client";

import { useEffect, useRef, useState } from "react";

import { boardCurrentJourney, cancelCurrentJourney, getCurrentArrivals } from "../lib/api";
import { buildBoardingLine } from "../lib/boarding-line";
import type {
  ActiveJourneySnapshot,
  ArrivingTrain,
  CurrentArrivalsResponse,
  OnboardTrain,
} from "../lib/types";
import { BoardingLine } from "./boarding-line";
import { Button } from "./ui/button";

type AwaitingBoardJourney = Extract<ActiveJourneySnapshot, { state: "awaiting_board" }>;

type TrainPickerProps = {
  journey: AwaitingBoardJourney;
  onJourneyRefresh: () => void;
};

const ARRIVALS_POLL_DELAY_MS = 15_000;
const ARRIVALS_LOAD_ERROR = "도착 열차 정보를 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.";
const BOARD_ERROR = "탑승을 시작하지 못했어요. 열차 정보를 새로고침한 뒤 다시 시도해 주세요.";
const CANCEL_ERROR = "여정을 취소하지 못했어요. 잠시 후 다시 시도해 주세요.";

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

function onboardStatusDisplay(train: OnboardTrain): string {
  const status: Record<OnboardTrain["status"], string> = {
    approaching: "접근 중",
    arrived: "도착",
    departed: "출발",
    between: "역 사이 이동 중",
  };
  return `현재 ${train.station_name} · ${status[train.status]}`;
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
  const [cancelling, setCancelling] = useState(false);
  const [cancelSucceeded, setCancelSucceeded] = useState(false);
  const [showCancelConfirmation, setShowCancelConfirmation] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancelControllerRef = useRef<AbortController | null>(null);
  const cancellingRef = useRef(false);

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
      cancelControllerRef.current?.abort();
      cancelControllerRef.current = null;
      cancellingRef.current = false;
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
    cancelControllerRef.current?.abort();
    cancelControllerRef.current = null;
    cancellingRef.current = false;
    setCancelling(false);
    setCancelSucceeded(false);
    setShowCancelConfirmation(false);
    setCancelError(null);
  }, [journey.journey_id, journey.leg_idx]);

  const board = async (trainNo: string | null, retroactive: boolean) => {
    if (boardingRef.current || boardSucceeded || cancellingRef.current || cancelSucceeded) {
      return;
    }

    const controller = new AbortController();
    boardingRef.current = true;
    boardControllerRef.current = controller;
    setBoarding(true);
    setBoardError(null);

    let succeeded = false;
    try {
      await boardCurrentJourney(trainNo, retroactive, controller.signal);
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

  const performCancel = async () => {
    if (cancellingRef.current || cancelSucceeded || boardingRef.current || boardSucceeded) {
      return;
    }

    const controller = new AbortController();
    cancellingRef.current = true;
    cancelControllerRef.current = controller;
    setCancelling(true);
    setShowCancelConfirmation(false);
    setCancelError(null);

    let succeeded = false;
    try {
      await cancelCurrentJourney(controller.signal);
      if (cancelControllerRef.current === controller && !controller.signal.aborted) {
        succeeded = true;
        setCancelSucceeded(true);
        onJourneyRefresh();
      }
    } catch (error: unknown) {
      if (cancelControllerRef.current === controller && !controller.signal.aborted && !isAbortError(error)) {
        setCancelError(errorMessage(error, CANCEL_ERROR));
      }
    } finally {
      if (cancelControllerRef.current === controller && !controller.signal.aborted) {
        cancelControllerRef.current = null;
        if (!succeeded) {
          cancellingRef.current = false;
        }
        setCancelling(false);
      }
    }
  };

  const eligibleTrains = arrivals?.trains.filter((train) => train.matches_direction) ?? [];
  const eligibleOnboardTrains = arrivals?.already_onboard.filter((train) => train.matches_direction) ?? [];
  const hasEligibleTrains = eligibleTrains.length > 0 || eligibleOnboardTrains.length > 0;
  const boardingLocked = boarding || boardSucceeded || cancelling || cancelSucceeded;
  const boardingLine = arrivals
    ? buildBoardingLine(journey.leg.stations, journey.leg.start, arrivals.trains, arrivals.already_onboard)
    : null;

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

          {boardingLine ? <BoardingLine {...boardingLine} /> : null}

          {arrivalsLoading && arrivals === null ? <p className="train-picker__message" role="status">도착 열차를 확인하는 중이에요.</p> : null}
          {arrivalsError ? <p className="field-error" role="alert">{arrivalsError}</p> : null}
          {!arrivalsLoading && arrivals !== null && !hasEligibleTrains ? (
            <p className="train-picker__message" role="status">
              현재 진행 방향에 맞는 탑승 가능 열차가 없어요. 잠시 후 새로고침해 주세요.
            </p>
          ) : null}
          {eligibleTrains.length > 0 ? (
            <section className="train-picker__section">
              <h3 className="train-picker__section-heading">탑승 가능한 열차</h3>
              <ol aria-label="탑승 가능한 열차" className="train-picker__trains">
                {eligibleTrains.map((train) => (
                  <li key={train.train_no}>
                    <button
                      className="train-card"
                      disabled={boardingLocked}
                      onClick={() => void board(train.train_no, false)}
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
            </section>
          ) : null}
          {eligibleOnboardTrains.length > 0 ? (
            <section className="train-picker__section train-picker__section--onboard">
              <h3 className="train-picker__section-heading">이미 탑승한 열차</h3>
              <ol aria-label="이미 탑승한 열차" className="train-picker__trains">
                {eligibleOnboardTrains.map((train) => (
                  <li key={train.train_no}>
                    <button
                      className="train-card train-card--onboard"
                      disabled={boardingLocked}
                      onClick={() => void board(train.train_no, true)}
                      type="button"
                    >
                      <span className="train-card__header">
                        <span aria-level={3} className="train-card__number" role="heading">{train.train_no} 열차</span>
                        {train.is_express ? <span className="train-card__express">급행</span> : null}
                      </span>
                      <span className="train-card__direction">{train.direction_label}</span>
                      <span className="train-card__terminus">{train.terminus} 종착</span>
                      <span className="train-card__arrival">{onboardStatusDisplay(train)}</span>
                      <span className="train-card__warning">이전 이동 기록은 추정으로 재구성됩니다.</span>
                      <span className="train-card__action">
                        {boardSucceeded ? "탑승 확인 중이에요…" : boarding ? "탑승을 시작하는 중이에요…" : "이 열차 탑승"}
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
        </>
      ) : (
        <div className="train-picker__timer">
          <p>이 구간은 실시간 열차 위치를 지원하지 않아요.</p>
          <p>탑승을 시작하면 예정 이동 시간을 기준으로 여정을 안내합니다.</p>
          <Button disabled={boardingLocked} onClick={() => void board(null, false)}>
            {boardSucceeded ? "탑승 확인 중이에요…" : boarding ? "탑승을 시작하는 중이에요…" : "시간 기준으로 탑승 시작"}
          </Button>
        </div>
      )}

      {boardError ? <p className="field-error" role="alert">{boardError}</p> : null}

      <div className="train-picker__cancel">
        <Button
          aria-controls="train-picker-cancel-confirmation"
          aria-expanded={showCancelConfirmation}
          disabled={boardingLocked}
          onClick={() => setShowCancelConfirmation(true)}
          variant="ghost"
        >
          여정 취소
        </Button>
      </div>

      {showCancelConfirmation ? (
        <section
          aria-describedby="train-picker-cancel-description"
          aria-labelledby="train-picker-cancel-title"
          className="train-picker__cancel-confirmation"
          id="train-picker-cancel-confirmation"
        >
          <h4 id="train-picker-cancel-title">현재 여정을 취소할까요?</h4>
          <p id="train-picker-cancel-description">탑승 전 여정이 종료됩니다. 이 작업은 되돌릴 수 없어요.</p>
          <div>
            <Button disabled={boardingLocked} onClick={() => void performCancel()} variant="danger">
              {cancelling ? "여정을 취소하는 중이에요…" : "여정 취소 확인"}
            </Button>
            <Button disabled={boardingLocked} onClick={() => setShowCancelConfirmation(false)} variant="secondary">
              계속 탑승 준비할게요
            </Button>
          </div>
        </section>
      ) : null}

      {cancelError ? <p className="field-error" role="alert">{cancelError}</p> : null}
    </div>
  );
}
