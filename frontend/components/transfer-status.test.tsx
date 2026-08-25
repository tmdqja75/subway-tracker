import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { commitCurrentJourneyTimeline, retryCurrentJourneyPush } from "../lib/api";
import type { ActiveJourneySnapshot } from "../lib/types";
import { activeJourneySnapshot, pushFailedJourneySnapshot } from "../test/fixtures";
import { TransferStatus } from "./transfer-status";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, retryCurrentJourneyPush: vi.fn(), commitCurrentJourneyTimeline: vi.fn() };
});

vi.mock("./maps/completed-journey-map", () => ({
  CompletedJourneyMap: () => <div data-testid="completed-journey-map" />,
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

function transferJourney(
  state: "pushing" | "completed" | "push_failed",
  overrides: Partial<ActiveJourneySnapshot> = {},
) {
  const transfer = state === "push_failed"
    ? pushFailedJourneySnapshot.transfer
    : state === "completed"
      ? { sent_points: 5, total_points: 5, remaining_points: 0, progress_percent: 100 }
      : { sent_points: 2, total_points: 5, remaining_points: 3, progress_percent: 40 };
  return { ...activeJourneySnapshot, ...overrides, state, transfer: overrides.transfer ?? transfer } as Extract<
    ActiveJourneySnapshot,
    { state: typeof state }
  >;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TransferStatus", () => {
  it("renders pushing progress solely from the authoritative backend values", () => {
    render(<TransferStatus journey={transferJourney("pushing")} onJourneyRefresh={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "이동 기록을 전송하고 있어요" })).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemin", "0");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "100");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByText("전송한 위치 2 / 5개 · 남은 위치 3개")).toBeVisible();
    expect(screen.getByText("40%")).toBeVisible();
    expect(screen.queryByRole("button", { name: /다시 전송/ })).not.toBeInTheDocument();
  });

  it("shows the backend-confirmed completed state and its 100 percent values", () => {
    render(<TransferStatus journey={transferJourney("completed")} onJourneyRefresh={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "이동 기록 전송이 완료됐어요" })).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("전송한 위치 5 / 5개 · 남은 위치 0개")).toBeVisible();
    expect(screen.getByText("100%")).toBeVisible();
    expect(screen.getByTestId("completed-journey-map")).toBeVisible();
  });

  it("offers a native next-journey button only for completed journeys and invokes it once", () => {
    const onBeginNextJourney = vi.fn();
    const { rerender } = render(
      <TransferStatus
        journey={transferJourney("completed")}
        onBeginNextJourney={onBeginNextJourney}
        onJourneyRefresh={vi.fn()}
      />,
    );

    const nextJourneyButton = screen.getByRole("button", { name: "새 여정 시작하기" });
    expect(nextJourneyButton.tagName).toBe("BUTTON");
    fireEvent.click(nextJourneyButton);
    expect(onBeginNextJourney).toHaveBeenCalledTimes(1);

    rerender(<TransferStatus journey={transferJourney("pushing")} onJourneyRefresh={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "새 여정 시작하기" })).not.toBeInTheDocument();

    rerender(<TransferStatus journey={transferJourney("push_failed")} onJourneyRefresh={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "새 여정 시작하기" })).not.toBeInTheDocument();
  });

  it("lets a failed transfer return to station selection for a later retry without sending again", () => {
    const onDeferPush = vi.fn();
    render(
      <TransferStatus
        journey={transferJourney("push_failed")}
        onDeferPush={onDeferPush}
        onJourneyRefresh={vi.fn()}
      />,
    );

    const deferPushButton = screen.getByRole("button", { name: "데이터 나중에 다시 보내기" });
    expect(deferPushButton.tagName).toBe("BUTTON");
    fireEvent.click(deferPushButton);

    expect(onDeferPush).toHaveBeenCalledTimes(1);
    expect(retryCurrentJourneyPush).not.toHaveBeenCalled();
  });

  it("explains failed delivery with the backend message, technical detail, and retained records", () => {
    render(<TransferStatus journey={transferJourney("push_failed")} onJourneyRefresh={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("전송 중 네트워크 오류가 발생했어요.");
    expect(screen.getByText("기술 정보: upstream timed out")).toBeVisible();
    expect(screen.getByText("이동 기록은 보관되어 있어요. 전송한 위치 3 / 5개와 남은 위치 2개를 다시 전송할 수 있어요.")).toBeVisible();
    expect(screen.getByTestId("completed-journey-map")).toBeVisible();
  });

  it("prevents duplicate retry while pending, reports backend errors, and refreshes only after success", async () => {
    const retry = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    vi.mocked(retryCurrentJourneyPush)
      .mockReturnValueOnce(retry.promise)
      .mockRejectedValueOnce(new Error("저장된 기록을 다시 전송할 수 없어요."));
    const { rerender } = render(<TransferStatus journey={transferJourney("push_failed")} onJourneyRefresh={onJourneyRefresh} />);

    const button = screen.getByRole("button", { name: "기록을 다시 전송" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(retryCurrentJourneyPush).toHaveBeenCalledTimes(1);
    expect(retryCurrentJourneyPush).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "기록을 다시 전송하는 중이에요…" })).toBeDisabled();

    await act(async () => {
      retry.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(onJourneyRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "여정 상태를 새로고침하는 중이에요…" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "여정 상태를 새로고침하는 중이에요…" }));
    expect(retryCurrentJourneyPush).toHaveBeenCalledTimes(1);

    rerender(
      <TransferStatus
        journey={transferJourney("push_failed", { transfer: { ...pushFailedJourneySnapshot.transfer, sent_points: 4, remaining_points: 1, progress_percent: 80 } })}
        onJourneyRefresh={onJourneyRefresh}
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "기록을 다시 전송" })).toBeEnabled());
  });

  it("unlocks after a retry error so the rider can retry", async () => {
    const onJourneyRefresh = vi.fn();
    vi.mocked(retryCurrentJourneyPush).mockRejectedValueOnce(new Error("저장된 기록을 다시 전송할 수 없어요."));
    render(<TransferStatus journey={transferJourney("push_failed")} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "기록을 다시 전송" }));
    expect(await screen.findByText("저장된 기록을 다시 전송할 수 없어요.")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "기록을 다시 전송" })).toBeEnabled());
    expect(onJourneyRefresh).not.toHaveBeenCalled();
  });

  it("merges a completed journey onto the main timeline and disables the button once done", async () => {
    const commit = deferred<{ ok: true }>();
    vi.mocked(commitCurrentJourneyTimeline).mockReturnValueOnce(commit.promise);
    render(<TransferStatus journey={transferJourney("completed")} onJourneyRefresh={vi.fn()} />);

    const button = screen.getByRole("button", { name: "메인 타임라인에 병합" });
    fireEvent.click(button);
    expect(commitCurrentJourneyTimeline).toHaveBeenCalledTimes(1);
    expect(commitCurrentJourneyTimeline).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "메인 타임라인에 병합하는 중이에요…" })).toBeDisabled();

    await act(async () => {
      commit.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "메인 타임라인에 병합했어요" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "메인 타임라인에 병합했어요" }));
    expect(commitCurrentJourneyTimeline).toHaveBeenCalledTimes(1);
  });

  it("shows a commit error and lets the rider try again", async () => {
    vi.mocked(commitCurrentJourneyTimeline).mockRejectedValueOnce(new Error("기록을 병합하지 못했어요."));
    render(<TransferStatus journey={transferJourney("completed")} onJourneyRefresh={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "메인 타임라인에 병합" }));
    expect(await screen.findByText("기록을 병합하지 못했어요.")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "메인 타임라인에 병합" })).toBeEnabled());
  });

  it("aborts an in-flight retry on unmount without a late refresh or state update", async () => {
    const retry = deferred<{ ok: true }>();
    const onJourneyRefresh = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(retryCurrentJourneyPush).mockReturnValue(retry.promise);
    const { unmount } = render(<TransferStatus journey={transferJourney("push_failed")} onJourneyRefresh={onJourneyRefresh} />);

    fireEvent.click(screen.getByRole("button", { name: "기록을 다시 전송" }));
    const signal = vi.mocked(retryCurrentJourneyPush).mock.calls[0][0]!;
    unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      retry.resolve({ ok: true });
      await Promise.resolve();
    });
    expect(onJourneyRefresh).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
