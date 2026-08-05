import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { startJourney } from "../lib/api";
import type { Itinerary } from "../lib/types";
import { itinerary } from "../test/fixtures";
import { RouteList } from "./route-list";

vi.mock("../lib/api", () => ({
  startJourney: vi.fn(),
}));

const uncoveredItinerary: Itinerary = {
  ...itinerary,
  total_time: 660,
  transfer_count: 1,
  total_walk_time: 120,
  fare: null,
  summary: ["강남에서 2호선 탑승", "환승 후 공항철도 이용"],
  legs: itinerary.legs.map((leg) => ({ ...leg, line_key: null })),
};

const busItinerary: Itinerary = {
  ...itinerary,
  total_time: 900,
  summary: ["강남에서 146번 버스 탑승"],
  legs: itinerary.legs.map((leg) => ({ ...leg, route: "146", line_key: null, mode: "BUS" })),
};

function deferred<T>() {
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((_resolve, rejectPromise) => {
    reject = rejectPromise;
  });

  return { promise, reject };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RouteList", () => {
  it("renders every returned route as a touch-friendly semantic option with key journey details", () => {
    render(<RouteList itineraries={[itinerary, uncoveredItinerary]} onBack={vi.fn()} onStarted={vi.fn()} />);

    const routes = screen.getByRole("list", { name: "추천 경로" });
    const routeItems = within(routes).getAllByRole("listitem");
    const routeCards = within(routes).getAllByRole("button");
    expect(routeItems).toHaveLength(2);
    expect(routeCards).toHaveLength(2);
    expect(routeCards[0].tagName).toBe("BUTTON");
    expect(routeCards[0].parentElement).toBe(routeItems[0]);
    expect(routeCards[1].tagName).toBe("BUTTON");
    expect(routeCards[1].parentElement).toBe(routeItems[1]);
    expect(screen.getByText("소요 7분")).toBeVisible();
    expect(screen.getByText("환승 0회")).toBeVisible();
    expect(screen.getByText("도보 0분")).toBeVisible();
    expect(screen.getByText("1,400원")).toBeVisible();
    expect(screen.getByText("강남에서 2호선 탑승")).toBeVisible();
    expect(screen.getByText("실시간 안내 미지원 구간이 있어 시간 기준으로 안내됩니다.")).toBeVisible();
    expect(screen.getByText("요금 정보 없음")).toBeVisible();
  });

  it("filters out itineraries with a bus leg", () => {
    render(<RouteList itineraries={[itinerary, busItinerary]} onBack={vi.fn()} onStarted={vi.fn()} />);

    const routes = screen.getByRole("list", { name: "추천 경로" });
    expect(within(routes).getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByText("강남에서 146번 버스 탑승")).not.toBeInTheDocument();
  });

  it("shows the empty state when every itinerary contains a bus leg", () => {
    render(<RouteList itineraries={[busItinerary]} onBack={vi.fn()} onStarted={vi.fn()} />);

    expect(screen.getByText("검색 결과에 맞는 경로가 없어요. 역 이름을 다시 확인해 주세요.")).toBeVisible();
  });

  it("starts exactly the selected itinerary and prevents duplicate starts while pending", async () => {
    let resolveStart!: (value: { journey_id: number; state: "awaiting_board" }) => void;
    vi.mocked(startJourney).mockReturnValue(
      new Promise((resolve) => {
        resolveStart = resolve;
      }),
    );
    const onStarted = vi.fn();
    render(<RouteList itineraries={[itinerary, uncoveredItinerary]} onBack={vi.fn()} onStarted={onStarted} />);

    const routes = screen.getByRole("list", { name: "추천 경로" });
    const cards = within(routes).getAllByRole("button");
    fireEvent.click(within(cards[1]).getByText("요금 정보 없음"));

    expect(startJourney).toHaveBeenCalledWith({ itinerary: uncoveredItinerary }, expect.any(AbortSignal));
    expect(startJourney).toHaveBeenCalledTimes(1);
    expect(cards[0]).toBeDisabled();
    expect(cards[1]).toBeDisabled();
    expect(cards[1]).toHaveTextContent("여정을 시작하는 중이에요…");

    resolveStart({ journey_id: 42, state: "awaiting_board" });
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1));
  });

  it("keeps start errors inline and allows the rider to return to search", async () => {
    vi.mocked(startJourney).mockRejectedValue(new Error("offline"));
    const onBack = vi.fn();
    render(<RouteList itineraries={[itinerary]} onBack={onBack} onStarted={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /이 경로로 시작/ }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("여정을 시작하지 못했어요. 잠시 후 다시 시도해 주세요."),
    );

    fireEvent.click(screen.getByRole("button", { name: "출발역·도착역 다시 입력" }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("aborts a pending journey start on unmount and ignores its later rejection", async () => {
    const request = deferred<{ journey_id: number; state: "awaiting_board" }>();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(startJourney).mockReturnValue(request.promise);
    const { unmount } = render(<RouteList itineraries={[itinerary]} onBack={vi.fn()} onStarted={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /이 경로로 시작/ }));
    const signal = vi.mocked(startJourney).mock.calls[0][1]!;

    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      request.reject(new Error("offline"));
      await Promise.resolve();
    });
    expect(consoleError).not.toHaveBeenCalled();
  });
});
