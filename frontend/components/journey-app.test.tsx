import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCurrentJourney } from "../hooks/use-current-journey";
import type { Itinerary } from "../lib/types";
import { activeJourneySnapshot, idleJourneySnapshot, itinerary } from "../test/fixtures";
import { JourneyApp } from "./journey-app";

vi.mock("../hooks/use-current-journey", () => ({
  useCurrentJourney: vi.fn(),
}));

vi.mock("./journey-search", () => ({
  JourneySearch: ({ onRoutes }: { onRoutes: (routes: Itinerary[]) => void }) => (
    <button onClick={() => onRoutes([itinerary])} type="button">
      경로 보이기
    </button>
  ),
}));

vi.mock("./route-list", () => ({
  RouteList: ({ onStarted }: { onStarted: () => void }) => (
    <button onClick={onStarted} type="button">
      경로 시작 성공
    </button>
  ),
}));

vi.mock("./live-journey", () => ({
  LiveJourney: ({ onJourneyRefresh }: { onJourneyRefresh: () => void }) => (
    <button onClick={onJourneyRefresh} type="button">
      실시간 여정 화면
    </button>
  ),
}));

vi.mock("./transfer-status", () => ({
  TransferStatus: ({
    journey,
    onJourneyRefresh,
    onBeginNextJourney,
  }: {
    journey: { state: string };
    onJourneyRefresh: () => void;
    onBeginNextJourney?: () => void;
  }) => (
    <>
      <button onClick={onJourneyRefresh} type="button">
        전송 상태: {journey.state}
      </button>
      <span data-has-begin-next={String(onBeginNextJourney !== undefined)} data-testid="transfer-begin-next" />
      {onBeginNextJourney ? (
        <button onClick={onBeginNextJourney} type="button">새 여정 시작하기</button>
      ) : null}
    </>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("JourneyApp", () => {
  it("uses the authoritative idle snapshot to show search and routes only on-train journeys to live tracking", () => {
    const refresh = vi.fn();
    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: idleJourneySnapshot,
      loading: false,
      error: null,
      refresh,
    });
    const { rerender } = render(<JourneyApp />);

    expect(screen.getByRole("button", { name: "경로 보이기" })).toBeVisible();
    expect(within(screen.getByRole("list", { name: "이동 단계" })).getAllByRole("listitem")[0]).toHaveAttribute("aria-current", "step");

    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: activeJourneySnapshot,
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);

    expect(screen.queryByRole("button", { name: "경로 보이기" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "실시간 여정 화면" })).toBeVisible();
    expect(within(screen.getByRole("list", { name: "이동 단계" })).getAllByRole("listitem")[3]).toHaveAttribute("aria-current", "step");

    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: {
        ...activeJourneySnapshot,
        state: "pushing",
        transfer: { sent_points: 1, total_points: 1, remaining_points: 0, progress_percent: 100 },
      },
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);
    expect(screen.queryByRole("button", { name: "실시간 여정 화면" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "전송 상태: pushing" })).toBeVisible();
    expect(screen.getByTestId("transfer-begin-next")).toHaveAttribute("data-has-begin-next", "false");

    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: {
        ...activeJourneySnapshot,
        state: "completed",
        transfer: { sent_points: 1, total_points: 1, remaining_points: 0, progress_percent: 100 },
      },
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);
    expect(screen.getByRole("button", { name: "전송 상태: completed" })).toBeVisible();
    expect(screen.getByTestId("transfer-begin-next")).toHaveAttribute("data-has-begin-next", "true");

    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: {
        ...activeJourneySnapshot,
        state: "push_failed",
        transfer: {
          sent_points: 0,
          total_points: 1,
          remaining_points: 1,
          progress_percent: 0,
          reason: "network",
          message: "전송에 실패했어요.",
          detail: "timeout",
          can_retry: true,
        },
      },
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);
    expect(screen.getByRole("button", { name: "전송 상태: push_failed" })).toBeVisible();
    expect(screen.getByTestId("transfer-begin-next")).toHaveAttribute("data-has-begin-next", "false");
  });

  it("uses a completed-only presentation mode to start the next journey from station search", () => {
    const refresh = vi.fn();
    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: idleJourneySnapshot,
      loading: false,
      error: null,
      refresh,
    });
    const { rerender } = render(<JourneyApp />);

    fireEvent.click(screen.getByRole("button", { name: "경로 보이기" }));
    expect(screen.getByRole("button", { name: "경로 시작 성공" })).toBeVisible();

    const completedSnapshot = {
      ...activeJourneySnapshot,
      state: "completed" as const,
      transfer: { sent_points: 1, total_points: 1, remaining_points: 0, progress_percent: 100 },
    };
    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: completedSnapshot,
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);

    expect(screen.getByRole("button", { name: "전송 상태: completed" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "새 여정 시작하기" }));

    expect(screen.getByRole("button", { name: "경로 보이기" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "경로 시작 성공" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "전송 상태: completed" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "이동 단계" })).getAllByRole("listitem")[0]).toHaveAttribute("aria-current", "step");

    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: activeJourneySnapshot,
      loading: false,
      error: null,
      refresh,
    });
    rerender(<JourneyApp />);

    expect(screen.getByRole("button", { name: "실시간 여정 화면" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "경로 보이기" })).not.toBeInTheDocument();
  });

  it("refreshes the authoritative hook after a route has started successfully", () => {
    const refresh = vi.fn();
    vi.mocked(useCurrentJourney).mockReturnValue({
      snapshot: idleJourneySnapshot,
      loading: false,
      error: null,
      refresh,
    });
    render(<JourneyApp />);

    fireEvent.click(screen.getByRole("button", { name: "경로 보이기" }));
    fireEvent.click(screen.getByRole("button", { name: "경로 시작 성공" }));

    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
