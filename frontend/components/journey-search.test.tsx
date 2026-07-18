import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { searchRoutes, searchStations } from "../lib/api";
import type { Itinerary, Station } from "../lib/types";
import { itinerary, station } from "../test/fixtures";
import { JourneySearch } from "./journey-search";

vi.mock("../lib/api", () => ({
  searchRoutes: vi.fn(),
  searchStations: vi.fn(),
}));

const destination: Station = {
  ...station,
  station_id: "0214",
  name: "홍대입구",
  line: "2호선",
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
  vi.useRealTimers();
});

describe("JourneySearch", () => {
  it("validates both required labelled station fields without issuing a route request", () => {
    render(<JourneySearch onRoutes={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "경로 찾기" }));

    expect(screen.getByText("출발역을 입력해 주세요.")).toBeVisible();
    expect(screen.getByText("도착역을 입력해 주세요.")).toBeVisible();
    expect(searchRoutes).not.toHaveBeenCalled();
    const originInput = screen.getByRole("combobox", { name: "출발역" });
    const destinationInput = screen.getByRole("combobox", { name: "도착역" });
    const originError = screen.getByText("출발역을 입력해 주세요.");
    const destinationError = screen.getByText("도착역을 입력해 주세요.");
    expect(originInput).toHaveAttribute("required");
    expect(destinationInput).toHaveAttribute("required");
    expect(originInput).toHaveAttribute("aria-invalid", "true");
    expect(destinationInput).toHaveAttribute("aria-invalid", "true");
    expect(originInput.getAttribute("aria-describedby")).toContain(originError.id);
    expect(destinationInput.getAttribute("aria-describedby")).toContain(destinationError.id);
  });

  it("submits the exact selected names and station IDs to route search", async () => {
    vi.useFakeTimers();
    vi.mocked(searchStations).mockImplementation(async (query) =>
      query.includes("강남") ? [station] : [destination],
    );
    vi.mocked(searchRoutes).mockResolvedValue([itinerary]);
    const onRoutes = vi.fn();
    render(<JourneySearch onRoutes={onRoutes} />);

    const originInput = screen.getByRole("combobox", { name: "출발역" });
    fireEvent.change(originInput, { target: { value: "강남" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    const originOption = screen.getByRole("option", { name: "강남 2호선" });
    fireEvent.mouseDown(originOption);
    fireEvent.click(originOption);

    const destinationInput = screen.getByRole("combobox", { name: "도착역" });
    fireEvent.change(destinationInput, { target: { value: "홍대" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    const destinationOption = screen.getByRole("option", { name: "홍대입구 2호선" });
    fireEvent.mouseDown(destinationOption);
    fireEvent.click(destinationOption);

    fireEvent.click(screen.getByRole("button", { name: "경로 찾기" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(searchRoutes).toHaveBeenCalledWith(
      {
        start: "강남",
        end: "홍대입구",
        start_id: "0222",
        end_id: "0214",
      },
      expect.any(AbortSignal),
    );
    expect(onRoutes).toHaveBeenCalledWith([itinerary]);
  });

  it("disables duplicate submission while loading and renders route request errors inline", async () => {
    let rejectRequest!: (reason?: unknown) => void;
    vi.mocked(searchRoutes).mockReturnValue(
      new Promise<Itinerary[]>((_resolve, reject) => {
        rejectRequest = reject;
      }),
    );
    render(<JourneySearch onRoutes={vi.fn()} />);

    fireEvent.change(screen.getByRole("combobox", { name: "출발역" }), { target: { value: "강남" } });
    fireEvent.change(screen.getByRole("combobox", { name: "도착역" }), { target: { value: "홍대입구" } });
    fireEvent.click(screen.getByRole("button", { name: "경로 찾기" }));

    expect(screen.getByRole("button", { name: "경로를 찾는 중이에요…" })).toBeDisabled();
    expect(searchRoutes).toHaveBeenCalledTimes(1);

    rejectRequest(new Error("offline"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("경로를 찾지 못했어요. 잠시 후 다시 시도해 주세요."),
    );
    expect(screen.getByRole("button", { name: "경로 찾기" })).toBeEnabled();
  });

  it("aborts a pending route search on unmount and ignores its later rejection", async () => {
    const request = deferred<Itinerary[]>();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.mocked(searchRoutes).mockReturnValue(request.promise);
    const { unmount } = render(<JourneySearch onRoutes={vi.fn()} />);

    fireEvent.change(screen.getByRole("combobox", { name: "출발역" }), { target: { value: "강남" } });
    fireEvent.change(screen.getByRole("combobox", { name: "도착역" }), { target: { value: "홍대입구" } });
    fireEvent.click(screen.getByRole("button", { name: "경로 찾기" }));
    const signal = vi.mocked(searchRoutes).mock.calls[0][1]!;

    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      request.reject(new Error("offline"));
      await Promise.resolve();
    });
    expect(consoleError).not.toHaveBeenCalled();
  });
});
