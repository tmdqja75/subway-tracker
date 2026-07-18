"use client";

import { useEffect, useRef, useState } from "react";

import { retryCurrentJourneyPush } from "../lib/api";
import type { ActiveJourneySnapshot, PushFailureTransfer } from "../lib/types";
import { CompletedJourneyMap } from "./maps/completed-journey-map";
import { Button } from "./ui/button";

type TransferJourney = Extract<ActiveJourneySnapshot, { state: "pushing" | "completed" | "push_failed" }>;

type TransferStatusProps = {
  journey: TransferJourney;
  onJourneyRefresh: () => void;
  onBeginNextJourney?: () => void;
};

const RETRY_ERROR = "이동 기록을 다시 전송하지 못했어요. 잠시 후 다시 시도해 주세요.";

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

function retryErrorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : RETRY_ERROR;
}

function titleFor(state: TransferJourney["state"]): string {
  switch (state) {
    case "pushing":
      return "이동 기록을 전송하고 있어요";
    case "completed":
      return "이동 기록 전송이 완료됐어요";
    case "push_failed":
      return "이동 기록 전송을 다시 시도할 수 있어요";
  }
}

function copyFor(state: TransferJourney["state"]): string {
  switch (state) {
    case "pushing":
      return "기록된 이동 경로를 안전하게 전송하고 있어요.";
    case "completed":
      return "이동 기록이 모두 전송됐어요.";
    case "push_failed":
      return "전송하지 못한 기록은 보관된 상태예요.";
  }
}

function failureTransfer(journey: TransferJourney): PushFailureTransfer {
  return journey.transfer as PushFailureTransfer;
}

function snapshotKey(journey: TransferJourney): string {
  const transfer = journey.transfer;
  const failedTransfer = journey.state === "push_failed" ? failureTransfer(journey) : null;
  const failure = failedTransfer
    ? `${failedTransfer.reason}:${failedTransfer.message}:${failedTransfer.detail}:${failedTransfer.can_retry}`
    : "";
  return [
    journey.journey_id,
    journey.state,
    transfer.sent_points,
    transfer.total_points,
    transfer.remaining_points,
    transfer.progress_percent,
    failure,
  ].join(":");
}

/** Shows only backend-confirmed transfer progress and delegates retry to the persisted journey. */
export function TransferStatus({ journey, onJourneyRefresh, onBeginNextJourney }: TransferStatusProps) {
  const [retryPending, setRetryPending] = useState(false);
  const [retrySucceeded, setRetrySucceeded] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const retryControllerRef = useRef<AbortController | null>(null);
  const retryInFlightRef = useRef(false);
  const retrySequenceRef = useRef(0);
  const currentSnapshotKey = snapshotKey(journey);
  const transfer = journey.transfer;
  const locked = retryPending || retrySucceeded;

  useEffect(
    () => () => {
      retrySequenceRef.current += 1;
      retryControllerRef.current?.abort();
      retryControllerRef.current = null;
      retryInFlightRef.current = false;
    },
    [],
  );

  useEffect(() => {
    retrySequenceRef.current += 1;
    retryControllerRef.current?.abort();
    retryControllerRef.current = null;
    retryInFlightRef.current = false;
    setRetryPending(false);
    setRetrySucceeded(false);
    setRetryError(null);
  }, [currentSnapshotKey]);

  const retry = async () => {
    if (journey.state !== "push_failed" || retryInFlightRef.current || retrySucceeded) {
      return;
    }

    const controller = new AbortController();
    const retrySequence = ++retrySequenceRef.current;
    retryInFlightRef.current = true;
    retryControllerRef.current = controller;
    setRetryPending(true);
    setRetryError(null);

    try {
      await retryCurrentJourneyPush(controller.signal);
      if (retrySequence === retrySequenceRef.current && !controller.signal.aborted) {
        setRetrySucceeded(true);
        onJourneyRefresh();
      }
    } catch (error: unknown) {
      if (retrySequence === retrySequenceRef.current && !controller.signal.aborted && !isAbortError(error)) {
        setRetryError(retryErrorMessage(error));
      }
    } finally {
      if (retrySequence === retrySequenceRef.current && !controller.signal.aborted) {
        retryControllerRef.current = null;
        retryInFlightRef.current = false;
        setRetryPending(false);
      }
    }
  };

  const retryLabel = retryPending
    ? "기록을 다시 전송하는 중이에요…"
    : retrySucceeded
      ? "여정 상태를 새로고침하는 중이에요…"
      : "기록을 다시 전송";

  return (
    <section aria-labelledby="transfer-status-title" className="transfer-status">
      <div className={`transfer-status__summary transfer-status__summary--${journey.state}`} aria-live="polite">
        <p className="eyebrow">JOURNEY DELIVERY</p>
        <h3 id="transfer-status-title">{titleFor(journey.state)}</h3>
        <p>{copyFor(journey.state)}</p>
        <div
          aria-label="이동 기록 전송 진행률"
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={transfer.progress_percent}
          className="transfer-status__progress"
          role="progressbar"
        >
          <span className="transfer-status__progress-fill" style={{ width: `${transfer.progress_percent}%` }} />
        </div>
        <p className="transfer-status__percent">{transfer.progress_percent}%</p>
        <p className="transfer-status__metrics">전송한 위치 {transfer.sent_points} / {transfer.total_points}개 · 남은 위치 {transfer.remaining_points}개</p>
      </div>

      {journey.state === "push_failed" ? (
        <div className="transfer-status__failure" role="alert">
          <p>{failureTransfer(journey).message}</p>
          <p>기술 정보: {failureTransfer(journey).detail}</p>
          <p>이동 기록은 보관되어 있어요. 전송한 위치 {transfer.sent_points} / {transfer.total_points}개와 남은 위치 {transfer.remaining_points}개를 다시 전송할 수 있어요.</p>
          <Button disabled={locked} onClick={() => void retry()}>{retryLabel}</Button>
          {retryError ? <p className="field-error" role="alert">{retryError}</p> : null}
        </div>
      ) : null}

      {journey.state === "completed" && onBeginNextJourney ? (
        <Button onClick={onBeginNextJourney}>새 여정 시작하기</Button>
      ) : null}

      <CompletedJourneyMap journeyKey={String(journey.journey_id)} trip={journey.trip} />
    </section>
  );
}
