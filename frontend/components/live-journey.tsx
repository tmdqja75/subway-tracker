"use client";

import { useEffect, useRef, useState } from "react";

import {
  alightCurrentJourney,
  cancelCurrentJourney,
  markCurrentJourneyMissed,
} from "../lib/api";
import type { ActiveJourneySnapshot } from "../lib/types";
import { LiveJourneyMap } from "./maps/live-journey-map";
import { Button } from "./ui/button";
import { LastUpdatedLabel } from "./ui/last-updated-label";

type OnTrainJourney = Extract<ActiveJourneySnapshot, { state: "on_train" }>;

type LiveJourneyProps = {
  journey: OnTrainJourney;
  lastUpdatedAt?: number | null;
  onJourneyRefresh: () => void;
};

type JourneyAction = "alight" | "missed" | "cancel";

const ACTION_ERROR = "여정 상태를 바꾸지 못했어요. 잠시 후 다시 시도해 주세요.";

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError"
  );
}

function actionErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : ACTION_ERROR;
}

function trackingCopy(mode: OnTrainJourney["tracking_mode"]): string {
  if (mode === "realtime") {
    return "실시간 위치를 받아 여정을 기록하고 있어요.";
  }
  if (mode === "timer") {
    return "예정 이동 시간을 기준으로 여정을 기록하고 있어요.";
  }
  return "현재 추적 방식을 확인하고 있어요.";
}

function trainCopy(train: OnTrainJourney["train"]): string {
  if (!train) {
    return "현재 열차 위치를 기다리고 있어요.";
  }
  return `${train.train_no} 열차 · ${train.station_name} · ${train.status}`;
}

export function LiveJourney({ journey, lastUpdatedAt = null, onJourneyRefresh }: LiveJourneyProps) {
  const [pendingAction, setPendingAction] = useState<JourneyAction | null>(null);
  const [actionSucceeded, setActionSucceeded] = useState(false);
  const [showCancelConfirmation, setShowCancelConfirmation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actionControllerRef = useRef<AbortController | null>(null);
  const actionInFlightRef = useRef(false);
  const journeyKey = `${journey.journey_id}:${journey.leg_idx}`;
  const locked = pendingAction !== null || actionSucceeded;

  useEffect(
    () => () => {
      actionControllerRef.current?.abort();
      actionControllerRef.current = null;
      actionInFlightRef.current = false;
    },
    [],
  );

  useEffect(() => {
    actionControllerRef.current?.abort();
    actionControllerRef.current = null;
    actionInFlightRef.current = false;
    setPendingAction(null);
    setActionSucceeded(false);
    setShowCancelConfirmation(false);
    setError(null);
  }, [journeyKey]);

  const performAction = async (action: JourneyAction) => {
    if (actionInFlightRef.current || actionSucceeded) {
      return;
    }

    const controller = new AbortController();
    actionInFlightRef.current = true;
    actionControllerRef.current = controller;
    setPendingAction(action);
    setShowCancelConfirmation(false);
    setError(null);

    try {
      if (action === "alight") {
        await alightCurrentJourney(controller.signal);
      } else if (action === "missed") {
        await markCurrentJourneyMissed(controller.signal);
      } else {
        await cancelCurrentJourney(controller.signal);
      }

      if (actionControllerRef.current === controller && !controller.signal.aborted) {
        setActionSucceeded(true);
        onJourneyRefresh();
      }
    } catch (nextError: unknown) {
      if (actionControllerRef.current === controller && !controller.signal.aborted && !isAbortError(nextError)) {
        setError(actionErrorMessage(nextError));
      }
    } finally {
      if (actionControllerRef.current === controller && !controller.signal.aborted) {
        actionControllerRef.current = null;
        actionInFlightRef.current = false;
        setPendingAction(null);
      }
    }
  };

  const primaryLabel = pendingAction === "alight"
    ? "잘 내렸는지 확인하는 중이에요…"
    : actionSucceeded
      ? "여정 상태를 새로고침하는 중이에요…"
      : "내렸어요";

  return (
    <section aria-labelledby="live-journey-title" className="live-journey">
      <div aria-live="polite" className="live-journey__summary" role="status">
        <p className="eyebrow">LIVE RIDE · 구간 {journey.leg_idx + 1} / {journey.leg_count}</p>
        <h3 id="live-journey-title">{journey.leg.route}</h3>
        <p className="live-journey__route">{journey.leg.start} → {journey.leg.end}</p>
        <p className="live-journey__train">{trainCopy(journey.train)}</p>
        <p>{trackingCopy(journey.tracking_mode)}</p>
        <LastUpdatedLabel updatedAt={lastUpdatedAt} />
        {journey.history_estimated ? (
          <p aria-label="추정 기록 안내" className="live-journey__history-notice" role="status">
            이전 이동 기록은 재구성한 추정치이며, 현재 추적은 실시간입니다.
          </p>
        ) : null}
        <p className="live-journey__points">기록된 위치 {journey.point_count}개</p>
      </div>

      <LiveJourneyMap journeyLegKey={journeyKey} leg={journey.leg} train={journey.train} />

      <div aria-label="여정 제어" className="live-journey__controls">
        <Button disabled={locked} onClick={() => void performAction("alight")}>
          {primaryLabel}
        </Button>
        <Button disabled={locked} onClick={() => void performAction("missed")} variant="secondary">
          {pendingAction === "missed" ? "열차를 다시 선택하는 중이에요…" : "다른 열차를 탔어요"}
        </Button>
        <Button
          aria-controls="cancel-journey-confirmation"
          aria-expanded={showCancelConfirmation}
          disabled={locked}
          onClick={() => setShowCancelConfirmation(true)}
          variant="ghost"
        >
          여정 취소
        </Button>
      </div>

      {showCancelConfirmation ? (
        <section
          aria-describedby="cancel-journey-description"
          aria-labelledby="cancel-journey-title"
          className="live-journey__cancel-confirmation"
          id="cancel-journey-confirmation"
        >
          <h4 id="cancel-journey-title">현재 여정을 취소할까요?</h4>
          <p id="cancel-journey-description">기록 중인 여정이 종료됩니다. 이 작업은 되돌릴 수 없어요.</p>
          <div>
            <Button disabled={locked} onClick={() => void performAction("cancel")} variant="danger">
              {pendingAction === "cancel" ? "여정을 취소하는 중이에요…" : "여정 취소 확인"}
            </Button>
            <Button disabled={locked} onClick={() => setShowCancelConfirmation(false)} variant="secondary">
              계속 이동할게요
            </Button>
          </div>
        </section>
      ) : null}

      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </section>
  );
}
