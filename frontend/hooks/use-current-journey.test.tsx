import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getCurrentJourney } from "../lib/api";
import type { CurrentJourneyResponse } from "../lib/types";
import {
  activeJourneySnapshot,
  idleJourneySnapshot,
  pushFailedJourneySnapshot,
} from "../test/fixtures";
import { useCurrentJourney } from "./use-current-journey";

vi.mock("../lib/api", () => ({
  getCurrentJourney: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

function snapshotFor(
  state: "awaiting_board" | "on_train" | "pushing" | "completed" | "push_failed",
): CurrentJourneyResponse {
  switch (state) {
    case "awaiting_board":
      return {
        ...activeJourneySnapshot,
        state,
        train: null,
        tracking_mode: null,
        transfer: null,
      };
    case "on_train":
      return activeJourneySnapshot;
    case "pushing":
    case "completed":
      return {
        ...activeJourneySnapshot,
        state,
        transfer: {
          sent_points: 2,
          total_points: 4,
          remaining_points: 2,
          progress_percent: 50,
        },
      };
    case "push_failed":
      return pushFailedJourneySnapshot;
  }
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("useCurrentJourney", () => {
  it("loads the authoritative current-journey snapshot immediately on mount", async () => {
    const request = deferred<CurrentJourneyResponse>();
    vi.mocked(getCurrentJourney).mockReturnValue(request.promise);

    const { result } = renderHook(() => useCurrentJourney());

    expect(result.current.loading).toBe(true);
    expect(result.current.snapshot).toBeNull();
    expect(getCurrentJourney).toHaveBeenCalledWith(expect.any(AbortSignal));

    await act(async () => {
      request.resolve(idleJourneySnapshot);
      await Promise.resolve();
    });

    expect(result.current).toMatchObject({
      snapshot: idleJourneySnapshot,
      loading: false,
      error: null,
    });
  });

  it("uses the returned state to schedule awaiting-board, on-train, and pushing refreshes", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentJourney)
      .mockResolvedValueOnce(snapshotFor("awaiting_board"))
      .mockResolvedValueOnce(snapshotFor("on_train"))
      .mockResolvedValueOnce(snapshotFor("pushing"))
      .mockResolvedValueOnce(snapshotFor("completed"));

    renderHook(() => useCurrentJourney());
    await flushPromises();
    expect(getCurrentJourney).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(3);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(4);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(4);
  });

  it.each([
    ["idle", idleJourneySnapshot],
    ["completed", snapshotFor("completed")],
    ["push_failed", snapshotFor("push_failed")],
  ] as const)("does not continue polling for terminal %s snapshots", async (_state, snapshot) => {
    vi.useFakeTimers();
    vi.mocked(getCurrentJourney).mockResolvedValue(snapshot);

    renderHook(() => useCurrentJourney());
    await flushPromises();
    expect(getCurrentJourney).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(1);
  });

  it("aborts and sequences a superseded refresh so the newest result wins", async () => {
    const first = deferred<CurrentJourneyResponse>();
    const second = deferred<CurrentJourneyResponse>();
    vi.mocked(getCurrentJourney).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useCurrentJourney());
    const firstSignal = vi.mocked(getCurrentJourney).mock.calls[0][0]!;

    await act(async () => {
      result.current.refresh();
    });
    const secondSignal = vi.mocked(getCurrentJourney).mock.calls[1][0]!;
    expect(firstSignal.aborted).toBe(true);
    expect(secondSignal.aborted).toBe(false);

    await act(async () => {
      second.resolve(snapshotFor("on_train"));
      await Promise.resolve();
    });
    expect(result.current.snapshot).toEqual(snapshotFor("on_train"));

    await act(async () => {
      first.resolve(idleJourneySnapshot);
      await Promise.resolve();
    });
    expect(result.current.snapshot).toEqual(snapshotFor("on_train"));
  });

  it("aborts the in-flight request on unmount and ignores its later rejection", async () => {
    const request = deferred<CurrentJourneyResponse>();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(getCurrentJourney).mockReturnValue(request.promise);
    const { unmount } = renderHook(() => useCurrentJourney());
    const signal = vi.mocked(getCurrentJourney).mock.calls[0][0]!;

    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      request.reject(new Error("offline"));
      await Promise.resolve();
    });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("retains the last valid snapshot after a transient error and retries at its active-state cadence", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentJourney)
      .mockResolvedValueOnce(snapshotFor("on_train"))
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(idleJourneySnapshot);
    const { result } = renderHook(() => useCurrentJourney());

    await flushPromises();
    expect(result.current.snapshot).toEqual(snapshotFor("on_train"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(2);
    expect(result.current.snapshot).toEqual(snapshotFor("on_train"));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe("여정 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(getCurrentJourney).toHaveBeenCalledTimes(3);
    expect(result.current).toMatchObject({
      snapshot: idleJourneySnapshot,
      loading: false,
      error: null,
    });
  });
});
