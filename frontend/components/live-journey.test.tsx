import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  alightCurrentJourney,
  cancelCurrentJourney,
  markCurrentJourneyMissed,
} from "../lib/api";
import type { ActiveJourneySnapshot } from "../lib/types";
import { activeJourneySnapshot } from "../test/fixtures";
import { LiveJourney } from "./live-journey";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    alightCurrentJourney: vi.fn(),
    cancelCurrentJourney: vi.fn(),
    markCurrentJourneyMissed: vi.fn(),
  };
});

vi.mock("./maps/live-journey-map", () => ({
  LiveJourneyMap: () => <div data-testid="live-journey-map" />,
}));

function deferred<T>() {
  let reject!: (reason?: unknown) => void;
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function onTrainJourney(overrides: Partial<Extract<ActiveJourneySnapshot, { state: "on_train" }>> = {}) {
  return { ...activeJourneySnapshot, ...overrides, state: "on_train", transfer: null } as Extract<
    ActiveJourneySnapshot,
    { state: "on_train" }
  >;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LiveJourney", () => {
  it("shows the authoritative leg, realtime train status, and recorded-point count", () => {
    render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={vi.fn()} />);

    expect(screen.getByRole("status")).toHaveTextContent("수도권2호선");
    expect(screen.getByRole("status")).toHaveTextContent("강남 → 역삼");
    expect(screen.getByRole("status")).toHaveTextContent("구간 1 / 1");
    expect(screen.getByRole("status")).toHaveTextContent("2221 열차");
    expect(screen.getByRole("status")).toHaveTextContent("강남");
    expect(screen.getByText("실시간 위치를 받아 여정을 기록하고 있어요.")).toBeVisible();
    expect(screen.getByText("기록된 위치 1개")).toBeVisible();
  });

  it("explains timer mode and safely communicates a waiting state without a train", () => {
    render(
      <LiveJourney
        journey={onTrainJourney({ tracking_mode: "timer", train: null, point_count: 4 })}
        onJourneyRefresh={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("현재 열차 위치를 기다리고 있어요.");
    expect(screen.getByText("예정 이동 시간을 기준으로 여정을 기록하고 있어요.")).toBeVisible();
    expect(screen.getByText("기록된 위치 4개")).toBeVisible();
  });

  it("discloses reconstructed early history while distinguishing live tracking", () => {
    render(<LiveJourney journey={onTrainJourney({ history_estimated: true })} onJourneyRefresh={vi.fn()} />);

    expect(screen.getByRole("status", { name: "추정 기록 안내" })).toHaveTextContent(
      "이전 이동 기록은 재구성한 추정치이며, 현재 추적은 실시간입니다.",
    );
  });

  it("submits only one primary action while pending and refreshes the authoritative snapshot after success", async () => {
    const action = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    vi.mocked(alightCurrentJourney).mockReturnValue(action.promise);
    render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    const alight = screen.getByRole("button", { name: "내렸어요" });
    fireEvent.click(alight);
    fireEvent.click(alight);
    expect(alightCurrentJourney).toHaveBeenCalledTimes(1);
    expect(alightCurrentJourney).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "잘 내렸는지 확인하는 중이에요…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "다른 열차를 탔어요" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "여정 취소" })).toBeDisabled();

    await act(async () => {
      action.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(onJourneyRefresh).toHaveBeenCalledTimes(1);
  });

  it("submits missed-train recovery and displays safe backend errors inline", async () => {
    const onJourneyRefresh = vi.fn();
    vi.mocked(markCurrentJourneyMissed)
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error("현재 상태에서는 열차를 다시 선택할 수 없어요."));
    const { rerender } = render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "다른 열차를 탔어요" }));
    await waitFor(() => expect(onJourneyRefresh).toHaveBeenCalledTimes(1));
    expect(markCurrentJourneyMissed).toHaveBeenCalledWith(expect.any(AbortSignal));

    rerender(<LiveJourney journey={{ ...onTrainJourney(), journey_id: 2 }} onJourneyRefresh={onJourneyRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: "다른 열차를 탔어요" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("현재 상태에서는 열차를 다시 선택할 수 없어요.");
  });

  it("uses a labelled inline cancellation confirmation without dialog semantics", async () => {
    const onJourneyRefresh = vi.fn();
    vi.mocked(cancelCurrentJourney).mockResolvedValue({ ok: true });
    render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    const openConfirmation = screen.getByRole("button", { name: "여정 취소" });
    expect(openConfirmation.tagName).toBe("BUTTON");
    expect(openConfirmation).toHaveAttribute("aria-expanded", "false");
    expect(openConfirmation).toHaveAttribute("aria-controls", "cancel-journey-confirmation");
    fireEvent.click(openConfirmation);
    const confirmation = screen.getByRole("region", { name: "현재 여정을 취소할까요?" });
    const confirm = screen.getByRole("button", { name: "여정 취소 확인" });
    const dismiss = screen.getByRole("button", { name: "계속 이동할게요" });
    expect(openConfirmation).toHaveAttribute("aria-expanded", "true");
    expect(confirm.tagName).toBe("BUTTON");
    expect(confirmation).toHaveTextContent("기록 중인 여정이 종료됩니다.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    fireEvent.click(dismiss);
    expect(openConfirmation).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "현재 여정을 취소할까요?" })).not.toBeInTheDocument();

    fireEvent.click(openConfirmation);
    fireEvent.click(screen.getByRole("button", { name: "여정 취소 확인" }));
    await waitFor(() => expect(cancelCurrentJourney).toHaveBeenCalledWith(expect.any(AbortSignal)));
    expect(onJourneyRefresh).toHaveBeenCalledTimes(1);
  });

  it("keeps the confirmed-success lock for a same-leg rerender and resets it for a new leg", async () => {
    const onJourneyRefresh = vi.fn();
    vi.mocked(alightCurrentJourney).mockResolvedValue({ ok: true });
    const { rerender } = render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "내렸어요" }));
    await waitFor(() => expect(onJourneyRefresh).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "여정 상태를 새로고침하는 중이에요…" })).toBeDisabled();

    rerender(
      <LiveJourney
        journey={onTrainJourney({ train: { ...activeJourneySnapshot.train!, status: "다음 역 진입" } })}
        onJourneyRefresh={onJourneyRefresh}
      />,
    );
    expect(screen.getByRole("button", { name: "여정 상태를 새로고침하는 중이에요…" })).toBeDisabled();

    rerender(<LiveJourney journey={onTrainJourney({ leg_idx: 1 })} onJourneyRefresh={onJourneyRefresh} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "내렸어요" })).toBeEnabled());
  });

  it("unlocks an action failure so the rider can retry", async () => {
    const onJourneyRefresh = vi.fn();
    vi.mocked(markCurrentJourneyMissed)
      .mockRejectedValueOnce(new Error("현재 상태에서는 열차를 다시 선택할 수 없어요."))
      .mockResolvedValueOnce({ ok: true });
    render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    const retry = screen.getByRole("button", { name: "다른 열차를 탔어요" });
    fireEvent.click(retry);
    expect(await screen.findByRole("alert")).toHaveTextContent("현재 상태에서는 열차를 다시 선택할 수 없어요.");
    expect(retry).toBeEnabled();

    fireEvent.click(retry);
    await waitFor(() => expect(markCurrentJourneyMissed).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onJourneyRefresh).toHaveBeenCalledTimes(1));
  });

  it("aborts an in-flight action on unmount without a late refresh or state update", async () => {
    const action = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(alightCurrentJourney).mockReturnValue(action.promise);
    const { unmount } = render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "내렸어요" }));
    const signal = vi.mocked(alightCurrentJourney).mock.calls[0][0]!;
    unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      action.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(onJourneyRefresh).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("ignores a late rejected action after unmount without a refresh or state-update warning", async () => {
    const action = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(alightCurrentJourney).mockReturnValue(action.promise);
    const { unmount } = render(<LiveJourney journey={onTrainJourney()} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "내렸어요" }));
    const signal = vi.mocked(alightCurrentJourney).mock.calls[0][0]!;
    unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      action.reject(new Error("late failure"));
      await Promise.resolve();
    });
    expect(onJourneyRefresh).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
