import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, boardCurrentJourney, getCurrentArrivals } from "../lib/api";
import type { ActiveJourneySnapshot, CurrentArrivalsResponse } from "../lib/types";
import { activeJourneySnapshot, arrivingTrain } from "../test/fixtures";
import { TrainPicker } from "./train-picker";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    boardCurrentJourney: vi.fn(),
    getCurrentArrivals: vi.fn(),
  };
});

function deferred<T>() {
  let reject!: (reason?: unknown) => void;
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

function awaitingSnapshot(covered = true): Extract<ActiveJourneySnapshot, { state: "awaiting_board" }> {
  return {
    ...activeJourneySnapshot,
    state: "awaiting_board",
    leg: { ...activeJourneySnapshot.leg, covered },
    train: null,
    tracking_mode: null,
    transfer: null,
  };
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

describe("TrainPicker", () => {
  it("loads arrivals immediately and polls again every 15 seconds after each completed request", async () => {
    vi.useFakeTimers();
    vi.mocked(getCurrentArrivals).mockResolvedValue({ covered: true, trains: [arrivingTrain] });

    render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={vi.fn()} />);
    await flushPromises();

    expect(getCurrentArrivals).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /2221/ })).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(getCurrentArrivals).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getCurrentArrivals).toHaveBeenCalledTimes(2);
  });

  it("aborts and ignores a stale arrivals response after a manual refresh", async () => {
    const first = deferred<CurrentArrivalsResponse>();
    const second = deferred<CurrentArrivalsResponse>();
    vi.mocked(getCurrentArrivals).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={vi.fn()} />);
    const firstSignal = vi.mocked(getCurrentArrivals).mock.calls[0][0]!;

    fireEvent.click(screen.getByRole("button", { name: "열차 목록 새로고침" }));
    const secondSignal = vi.mocked(getCurrentArrivals).mock.calls[1][0]!;
    expect(firstSignal.aborted).toBe(true);
    expect(secondSignal.aborted).toBe(false);

    await act(async () => {
      second.resolve({ covered: true, trains: [{ ...arrivingTrain, train_no: "newest" }] });
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: /newest/ })).toBeVisible();

    await act(async () => {
      first.resolve({ covered: true, trains: [{ ...arrivingTrain, train_no: "stale" }] });
      await Promise.resolve();
    });
    expect(screen.queryByRole("button", { name: /stale/ })).not.toBeInTheDocument();
  });

  it("renders only direction-matched train cards and preserves arrival-count accuracy qualifiers", async () => {
    vi.mocked(getCurrentArrivals).mockResolvedValue({
      covered: true,
      trains: [
        { ...arrivingTrain, train_no: "entering", stations_away: 0, arrival_msg: "곧 도착" },
        { ...arrivingTrain, train_no: "estimated", stations_away: 2, stations_away_estimated: true },
        { ...arrivingTrain, train_no: "exact", stations_away: 1, stations_away_estimated: false },
        { ...arrivingTrain, train_no: "opposite", matches_direction: false, direction_label: "반대 방향" },
        {
          ...arrivingTrain,
          train_no: "message-only",
          stations_away: null,
          arrival_msg: "곧 도착",
          is_express: true,
        },
      ],
    });

    render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={vi.fn()} />);

    expect(await screen.findByRole("button", { name: /entering/ })).toHaveTextContent("진입");
    expect(screen.getByRole("button", { name: /estimated/ })).toHaveTextContent("약 2정거장 전");
    expect(screen.getByRole("button", { name: /exact/ })).toHaveTextContent("1정거장 전");
    expect(screen.getByRole("button", { name: /message-only/ })).toHaveTextContent("곧 도착");
    expect(screen.queryByRole("button", { name: /opposite/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("성수행 - 구의방면")).toHaveLength(4);
    expect(screen.getAllByText("성수 종착")).toHaveLength(4);
    expect(screen.getByText("급행")).toBeVisible();
  });

  it("uses timer mode without requesting arrivals for an uncovered leg", () => {
    render(<TrainPicker journey={awaitingSnapshot(false)} onJourneyRefresh={vi.fn()} />);

    expect(getCurrentArrivals).not.toHaveBeenCalled();
    expect(screen.getByText("이 구간은 실시간 열차 위치를 지원하지 않아요.")).toBeVisible();
    expect(screen.getByRole("button", { name: "시간 기준으로 탑승 시작" })).toBeVisible();
  });

  it("keeps boarding locked after success until a new journey leg is rendered", async () => {
    const board = deferred<{ ok: true }>();
    vi.mocked(getCurrentArrivals).mockResolvedValue({ covered: true, trains: [arrivingTrain] });
    vi.mocked(boardCurrentJourney).mockReturnValueOnce(board.promise).mockResolvedValueOnce({ ok: true });
    const onJourneyRefresh = vi.fn();
    const { rerender } = render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(await screen.findByRole("button", { name: /2221/ }));
    fireEvent.click(screen.getByRole("button", { name: /2221/ }));
    expect(boardCurrentJourney).toHaveBeenCalledTimes(1);
    expect(boardCurrentJourney).toHaveBeenCalledWith("2221", expect.any(AbortSignal));

    await act(async () => {
      board.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(onJourneyRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByText("탑승 확인 중이에요…")).toBeVisible();
    expect(screen.getByRole("button", { name: /2221/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "열차 목록 새로고침" })).toBeDisabled();
    rerender(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={onJourneyRefresh} />);
    expect(screen.getByRole("button", { name: /2221/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /2221/ }));
    expect(boardCurrentJourney).toHaveBeenCalledTimes(1);

    rerender(<TrainPicker journey={{ ...awaitingSnapshot(), leg_idx: 1, leg_count: 2 }} onJourneyRefresh={onJourneyRefresh} />);
    fireEvent.click(await screen.findByRole("button", { name: /2221/ }));
    expect(boardCurrentJourney).toHaveBeenCalledTimes(2);
  });

  it("submits null for timer-mode boarding", async () => {
    vi.mocked(boardCurrentJourney).mockResolvedValue({ ok: true });
    render(<TrainPicker journey={awaitingSnapshot(false)} onJourneyRefresh={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "시간 기준으로 탑승 시작" }));
    await waitFor(() => expect(boardCurrentJourney).toHaveBeenCalledWith(null, expect.any(AbortSignal)));
  });

  it("renders a stale-board error inline and unlocks for retry", async () => {
    vi.mocked(boardCurrentJourney)
      .mockRejectedValueOnce(new ApiError("선택한 열차는 더 이상 탑승할 수 없어요.", 409))
      .mockResolvedValueOnce({ ok: true });
    vi.mocked(getCurrentArrivals).mockResolvedValue({ covered: true, trains: [arrivingTrain] });
    render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: /2221/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("선택한 열차는 더 이상 탑승할 수 없어요.");
    expect(screen.getByRole("button", { name: /2221/ })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /2221/ }));
    await waitFor(() => expect(boardCurrentJourney).toHaveBeenCalledTimes(2));
  });

  it("aborts the in-flight arrivals request on unmount without applying its later rejection", async () => {
    const arrivals = deferred<CurrentArrivalsResponse>();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(getCurrentArrivals).mockReturnValue(arrivals.promise);
    const { unmount } = render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={vi.fn()} />);
    const signal = vi.mocked(getCurrentArrivals).mock.calls[0][0]!;

    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      arrivals.reject(new Error("offline"));
      await Promise.resolve();
    });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it.each(["success", "rejection"] as const)("aborts an in-flight board request on unmount and ignores its later %s", async (outcome) => {
    const board = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(getCurrentArrivals).mockResolvedValue({ covered: true, trains: [arrivingTrain] });
    vi.mocked(boardCurrentJourney).mockReturnValue(board.promise);
    const { unmount } = render(<TrainPicker journey={awaitingSnapshot()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(await screen.findByRole("button", { name: /2221/ }));
    const signal = vi.mocked(boardCurrentJourney).mock.calls[0][1]!;
    unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      if (outcome === "success") {
        board.resolve({ ok: true });
      } else {
        board.reject(new Error("offline"));
      }
      await Promise.resolve();
    });

    expect(onJourneyRefresh).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
