"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getCurrentJourney } from "../lib/api";
import type { CurrentJourneyResponse } from "../lib/types";

const INITIAL_RETRY_DELAY_MS = 15_000;
const JOURNEY_LOAD_ERROR = "여정 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.";

export interface CurrentJourneyState {
  snapshot: CurrentJourneyResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

function pollDelayFor(snapshot: CurrentJourneyResponse): number | null {
  switch (snapshot.state) {
    case "awaiting_board":
      return 15_000;
    case "on_train":
      return 5_000;
    case "pushing":
      return 500;
    case "idle":
    case "completed":
    case "push_failed":
      return null;
  }
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

/**
 * Reads the backend-authoritative active journey and polls only while its returned state is active.
 * A manual refresh replaces any older request, so stale responses cannot overwrite newer UI state.
 */
export function useCurrentJourney(): CurrentJourneyState {
  const [snapshot, setSnapshot] = useState<CurrentJourneyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const requestControllerRef = useRef<AbortController | null>(null);
  const requestSequenceRef = useRef(0);
  const snapshotRef = useRef<CurrentJourneyResponse | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const refreshRef = useRef<() => void>(() => undefined);

  const clearScheduledRefresh = useCallback(() => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const refresh = useCallback(() => {
    if (!mountedRef.current) {
      return;
    }

    clearScheduledRefresh();
    requestControllerRef.current?.abort();

    const controller = new AbortController();
    const requestSequence = ++requestSequenceRef.current;
    requestControllerRef.current = controller;
    setLoading(true);
    setError(null);

    const isCurrentRequest = () =>
      mountedRef.current &&
      requestSequence === requestSequenceRef.current &&
      !controller.signal.aborted;

    const scheduleRefresh = (delay: number | null) => {
      if (delay === null || !isCurrentRequest()) {
        return;
      }
      timeoutRef.current = window.setTimeout(() => {
        timeoutRef.current = null;
        refreshRef.current();
      }, delay);
    };

    void getCurrentJourney(controller.signal)
      .then((nextSnapshot) => {
        if (!isCurrentRequest()) {
          return;
        }

        snapshotRef.current = nextSnapshot;
        setSnapshot(nextSnapshot);
        setLoading(false);
        setError(null);
        scheduleRefresh(pollDelayFor(nextSnapshot));
      })
      .catch((requestError: unknown) => {
        if (!isCurrentRequest() || isAbortError(requestError)) {
          return;
        }

        setLoading(false);
        setError(JOURNEY_LOAD_ERROR);
        scheduleRefresh(
          snapshotRef.current === null
            ? INITIAL_RETRY_DELAY_MS
            : pollDelayFor(snapshotRef.current),
        );
      })
      .finally(() => {
        if (requestControllerRef.current === controller) {
          requestControllerRef.current = null;
        }
      });
  }, [clearScheduledRefresh]);

  refreshRef.current = refresh;

  useEffect(() => {
    mountedRef.current = true;
    refresh();

    return () => {
      mountedRef.current = false;
      requestSequenceRef.current += 1;
      clearScheduledRefresh();
      requestControllerRef.current?.abort();
      requestControllerRef.current = null;
    };
  }, [clearScheduledRefresh, refresh]);

  return { snapshot, loading, error, refresh };
}
