import { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { searchStations } from "../lib/api";
import type { Station } from "../lib/types";
import { station } from "../test/fixtures";
import { StationAutocomplete } from "./station-autocomplete";

vi.mock("../lib/api", () => ({
  searchStations: vi.fn(),
}));

const anotherStation: Station = {
  ...station,
  station_id: "0421",
  name: "왕십리",
  line: "경의중앙선",
};

const sameNameAlternate: Station = {
  ...station,
  station_id: "D07",
  name: "강남",
  line: "신분당선",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

function ControlledAutocomplete() {
  const [value, setValue] = useState("");
  const [selectedStation, setSelectedStation] = useState<Station | null>(null);

  return (
    <>
      <StationAutocomplete
        id="origin"
        label="출발역"
        onStationSelect={setSelectedStation}
        onValueChange={setValue}
        placeholder="출발역을 입력하세요"
        selectedStation={selectedStation}
        value={value}
      />
      <output data-testid="selected-station">{selectedStation?.station_id ?? "none"}</output>
    </>
  );
}

function ControlledAutocompleteWithValidation() {
  const [value, setValue] = useState("강남");
  const [selectedStation, setSelectedStation] = useState<Station | null>(station);

  return (
    <StationAutocomplete
      id="origin"
      label="출발역"
      onStationSelect={setSelectedStation}
      onValueChange={setValue}
      placeholder="출발역을 입력하세요"
      selectedStation={selectedStation}
      validationError="출발역을 확인해 주세요."
      value={value}
    />
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("StationAutocomplete", () => {
  it("debounces station lookups for 250ms and exposes a labelled combobox", async () => {
    vi.useFakeTimers();
    vi.mocked(searchStations).mockResolvedValue([station]);
    render(<ControlledAutocomplete />);

    const input = screen.getByRole("combobox", { name: "출발역" });
    fireEvent.change(input, { target: { value: "강남" } });

    expect(searchStations).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(249);
    });
    expect(searchStations).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(searchStations).toHaveBeenCalledWith("강남", expect.any(AbortSignal));
    expect(screen.getByRole("option", { name: "강남 2호선" })).toBeVisible();
    expect(input).toHaveAttribute("aria-controls", "origin-listbox");
  });

  it("cancels and sequences stale lookups so late results cannot replace the newest query", async () => {
    vi.useFakeTimers();
    const first = deferred<Station[]>();
    const second = deferred<Station[]>();
    vi.mocked(searchStations).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    render(<ControlledAutocomplete />);

    const input = screen.getByRole("combobox", { name: "출발역" });
    fireEvent.change(input, { target: { value: "강" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    const firstSignal = vi.mocked(searchStations).mock.calls[0][1]!;

    fireEvent.change(input, { target: { value: "왕" } });
    expect(firstSignal.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });

    await act(async () => {
      second.resolve([anotherStation]);
      await Promise.resolve();
    });
    expect(screen.getByRole("option", { name: "왕십리 경의중앙선" })).toBeVisible();

    await act(async () => {
      first.resolve([station]);
      await Promise.resolve();
    });
    expect(screen.queryByRole("option", { name: "강남 2호선" })).not.toBeInTheDocument();
  });

  it("selects a station with the keyboard, retains its ID, and invalidates that selection when typing", async () => {
    vi.useFakeTimers();
    vi.mocked(searchStations).mockResolvedValue([station]);
    render(<ControlledAutocomplete />);

    const input = screen.getByRole("combobox", { name: "출발역" });
    fireEvent.change(input, { target: { value: "강남" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    screen.getByRole("option", { name: "강남 2호선" });

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute("aria-activedescendant", "origin-option-0");
    fireEvent.keyDown(input, { key: "Enter" });

    expect(input).toHaveValue("강남");
    expect(screen.getByTestId("selected-station")).toHaveTextContent("0222");
    const selectedConfirmation = screen.getByText("선택됨: 강남 · 2호선");
    expect(selectedConfirmation).toHaveAttribute("role", "status");
    expect(input.getAttribute("aria-describedby")).toContain(selectedConfirmation.id);

    fireEvent.change(input, { target: { value: "강남역" } });
    expect(screen.getByTestId("selected-station")).toHaveTextContent("none");
    expect(screen.queryByText("선택됨: 강남 · 2호선")).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("lets a rider reselect a same-name station on a different line without editing the query", async () => {
    vi.useFakeTimers();
    vi.mocked(searchStations).mockResolvedValue([station, sameNameAlternate]);
    render(<ControlledAutocomplete />);

    const input = screen.getByRole("combobox", { name: "출발역" });
    fireEvent.change(input, { target: { value: "강남" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("selected-station")).toHaveTextContent("0222");

    fireEvent.click(screen.getByRole("button", { name: "다른 호선 선택" }));
    expect(input).toHaveFocus();
    expect(input).toHaveValue("강남");
    expect(screen.getByTestId("selected-station")).toHaveTextContent("none");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    fireEvent.click(screen.getByRole("option", { name: "강남 신분당선" }));

    expect(screen.getByTestId("selected-station")).toHaveTextContent("D07");
    expect(screen.getByText("선택됨: 강남 · 신분당선")).toBeVisible();
  });

  it("merges parent validation and selected-station descriptions on the combobox", () => {
    render(<ControlledAutocompleteWithValidation />);

    const input = screen.getByRole("combobox", { name: "출발역" });
    const validationError = screen.getByText("출발역을 확인해 주세요.");
    const selectedConfirmation = screen.getByText("선택됨: 강남 · 2호선");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")).toContain(validationError.id);
    expect(input.getAttribute("aria-describedby")).toContain(selectedConfirmation.id);
  });

  it("shows safe loading and inline error states", async () => {
    vi.useFakeTimers();
    const result = deferred<Station[]>();
    vi.mocked(searchStations).mockReturnValue(result.promise);
    render(<ControlledAutocomplete />);

    fireEvent.change(screen.getByRole("combobox", { name: "출발역" }), { target: { value: "강남" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(screen.getByText("역을 찾는 중이에요…")).toBeVisible();

    await act(async () => {
      result.reject(new Error("offline"));
      await Promise.resolve();
    });
    const lookupError = screen.getByRole("alert");
    expect(lookupError).toHaveTextContent("역을 찾지 못했어요.");
    expect(screen.getByRole("combobox", { name: "출발역" }).getAttribute("aria-describedby")).toContain(lookupError.id);
  });
});
