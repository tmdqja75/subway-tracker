import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Coordinate, JourneyTrip } from "../../lib/types";
import { activeJourneySnapshot } from "../../test/fixtures";
import { TransferStatus } from "../transfer-status";
import { CompletedJourneyMap, geometryForCompletedTrip } from "./completed-journey-map";

const leaflet = vi.hoisted(() => {
  const mapInstances: Array<{ fitBounds: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn>; setView: ReturnType<typeof vi.fn> }> = [];
  const markerInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }> = [];
  const polylineInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }> = [];
  const tileLayerInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }> = [];
  const map = vi.fn();
  const marker = vi.fn();
  const polyline = vi.fn();
  const tileLayer = vi.fn();
  const reset = () => {
    mapInstances.splice(0);
    markerInstances.splice(0);
    polylineInstances.splice(0);
    tileLayerInstances.splice(0);
    map.mockReset().mockImplementation(() => {
      const instance = { fitBounds: vi.fn(), remove: vi.fn(), setView: vi.fn() };
      mapInstances.push(instance);
      return instance;
    });
    marker.mockReset().mockImplementation(() => {
      const instance = { addTo: vi.fn(), remove: vi.fn() };
      markerInstances.push(instance);
      return instance;
    });
    polyline.mockReset().mockImplementation(() => {
      const instance = { addTo: vi.fn(), remove: vi.fn() };
      polylineInstances.push(instance);
      return instance;
    });
    tileLayer.mockReset().mockImplementation(() => {
      const instance = { addTo: vi.fn(), remove: vi.fn() };
      tileLayerInstances.push(instance);
      return instance;
    });
  };
  reset();
  return { latLngBounds: vi.fn(() => ({ isValid: () => true })), map, mapInstances, marker, markerInstances, polyline, polylineInstances, reset, tileLayer, tileLayerInstances };
});

vi.mock("leaflet", () => leaflet);

afterEach(() => {
  cleanup();
  leaflet.reset();
  vi.clearAllMocks();
});

describe("CompletedJourneyMap", () => {
  const trip: JourneyTrip = {
    legs: [
      {
        ...activeJourneySnapshot.trip.legs[0],
        transfer_walk_shape: [[37.5006, 127.0364], [37.501, 127.037]] as Coordinate[],
      },
      {
        route: "수도권9호선",
        start: "환승역",
        end: "종착역",
        shape: [[Number.NaN, 127.04] as Coordinate],
        stations: [
          { index: 0, name: "환승역", lat: 37.501, lon: 127.037 },
          { index: 1, name: "종착역", lat: 37.51, lon: 127.045 },
          { index: 2, name: "invalid", lat: Number.NaN, lon: 127.05 },
        ],
        transfer_walk_shape: [[37.51, 127.045], [37.511, 127.046]] as Coordinate[],
      },
    ],
  };

  it("composes ordered route shapes with station fallback and post-leg transfer walking geometry", () => {
    expect(geometryForCompletedTrip(trip)).toEqual([
      [37.4979, 127.0276],
      [37.5006, 127.0364],
      [37.5006, 127.0364],
      [37.501, 127.037],
      [37.501, 127.037],
      [37.51, 127.045],
      [37.51, 127.045],
      [37.511, 127.046],
    ]);
  });

  it("uses a client effect to render full geometry and cleans every resource on journey changes and unmount", async () => {
    const { rerender, unmount } = render(<CompletedJourneyMap journeyKey="1" trip={trip} />);

    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(1));
    expect(leaflet.polyline).toHaveBeenCalledWith(geometryForCompletedTrip(trip), expect.any(Object));
    expect(leaflet.marker).toHaveBeenNthCalledWith(1, [37.4979, 127.0276], expect.objectContaining({ alt: "출발지" }));
    expect(leaflet.marker).toHaveBeenNthCalledWith(2, [37.511, 127.046], expect.objectContaining({ alt: "도착지" }));
    expect(leaflet.mapInstances[0].fitBounds).toHaveBeenCalled();

    rerender(<CompletedJourneyMap journeyKey="2" trip={trip} />);
    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(2));
    expect(leaflet.markerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.markerInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.polylineInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[0].remove).toHaveBeenCalledTimes(1);

    unmount();
    expect(leaflet.markerInstances[2].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.markerInstances[3].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.polylineInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayerInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[1].remove).toHaveBeenCalledTimes(1);
  });

  it("keeps the Leaflet map through transfer progress polling and recreates it for a new journey identity", async () => {
    const pushingJourney = (journeyId: number, sentPoints: number) => ({
      ...activeJourneySnapshot,
      journey_id: journeyId,
      state: "pushing" as const,
      transfer: {
        sent_points: sentPoints,
        total_points: 5,
        remaining_points: 5 - sentPoints,
        progress_percent: sentPoints * 20,
      },
    });
    const onJourneyRefresh = vi.fn();
    const { rerender } = render(
      <TransferStatus journey={pushingJourney(1, 2)} onJourneyRefresh={onJourneyRefresh} />,
    );

    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(1));

    rerender(<TransferStatus journey={pushingJourney(1, 3)} onJourneyRefresh={onJourneyRefresh} />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(leaflet.map).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[0].remove).not.toHaveBeenCalled();

    rerender(<TransferStatus journey={pushingJourney(2, 3)} onJourneyRefresh={onJourneyRefresh} />);
    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(2));
    expect(leaflet.mapInstances[0].remove).toHaveBeenCalledTimes(1);
  });

  it("cleans partial setup failure, shows a fallback, and initializes a later journey safely", async () => {
    leaflet.marker.mockImplementationOnce(() => {
      throw new Error("marker setup failed");
    });
    const { rerender, unmount } = render(<CompletedJourneyMap journeyKey="1" trip={trip} />);

    expect(await screen.findByRole("status")).toHaveTextContent("전체 경로 지도를 표시하지 못했어요.");
    expect(leaflet.tileLayerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.polylineInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[0].remove).toHaveBeenCalledTimes(1);

    rerender(<CompletedJourneyMap journeyKey="2" trip={trip} />);
    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("전체 경로 지도를 표시하지 못했어요. 이동 기록 상태는 계속 안내할게요.")).not.toBeInTheDocument();

    unmount();
    expect(leaflet.mapInstances[1].remove).toHaveBeenCalledTimes(1);
  });
});
