import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Coordinate, JourneyLegSnapshot } from "../../lib/types";
import { activeJourneySnapshot } from "../../test/fixtures";
import { LiveJourneyMap, geometryForLiveLeg, positionForLiveTrain } from "./live-journey-map";

const leaflet = vi.hoisted(() => {
  const mapInstances: Array<{ fitBounds: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn>; setView: ReturnType<typeof vi.fn> }> = [];
  const markerInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn>; setLatLng: ReturnType<typeof vi.fn> }> = [];
  const polylineInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }> = [];
  const tileLayerInstances: Array<{ addTo: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> }> = [];
  const map = vi.fn();
  const divIcon = vi.fn();
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
    divIcon.mockReset().mockImplementation((options) => options);
    marker.mockReset().mockImplementation(() => {
      const instance = { addTo: vi.fn(), remove: vi.fn(), setLatLng: vi.fn() };
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
  return {
    latLngBounds: vi.fn(() => ({ isValid: () => true })),
    divIcon,
    map,
    mapInstances,
    marker,
    markerInstances,
    polyline,
    polylineInstances,
    reset,
    tileLayer,
    tileLayerInstances,
  };
});

vi.mock("leaflet", () => leaflet);

afterEach(() => {
  cleanup();
  leaflet.reset();
  vi.clearAllMocks();
});

describe("LiveJourneyMap", () => {
  it("uses valid backend shape geometry first and falls back to finite station coordinates", () => {
    expect(geometryForLiveLeg(activeJourneySnapshot.leg)).toEqual(activeJourneySnapshot.leg.shape);
    expect(geometryForLiveLeg({ ...activeJourneySnapshot.leg, shape: [] })).toEqual([
      [37.4979, 127.0276],
      [37.5006, 127.0364],
    ]);
    expect(
      geometryForLiveLeg({
        ...activeJourneySnapshot.leg,
        shape: [[37.4979, 127.0276], [Number.NaN, 127.0364]],
        stations: [
          ...activeJourneySnapshot.leg.stations,
          { index: 2, name: "invalid", lat: Number.NaN, lon: 127.05 },
        ],
      }),
    ).toEqual([
      [37.4979, 127.0276],
      [37.5006, 127.0364],
    ]);
  });

  it("projects the coarse backend position along the actual curved station segment", () => {
    const curvedLeg: Pick<JourneyLegSnapshot, "shape" | "stations"> = {
      ...activeJourneySnapshot.leg,
      stations: [
        { index: 0, name: "출발", lat: 0, lon: 0 },
        { index: 1, name: "경유", lat: 0, lon: 10 },
        { index: 2, name: "도착", lat: 0, lon: 20 },
      ],
      shape: [[0, 0], [5, 5], [0, 10], [5, 15], [0, 20]] as Coordinate[],
    };

    expect(positionForLiveTrain(curvedLeg, {
      ...activeJourneySnapshot.train!,
      station_index: 1,
      status: "between",
      lat: 0,
      lon: 5,
    })).toEqual([5, 5]);
    expect(positionForLiveTrain(curvedLeg, {
      ...activeJourneySnapshot.train!,
      station_index: 1,
      status: "estimated",
      lat: 0,
      lon: 15,
    })).toEqual([5, 15]);
    expect(positionForLiveTrain({ ...curvedLeg, shape: [] }, {
      ...activeJourneySnapshot.train!,
      station_index: null,
      lat: 3,
      lon: 4,
    })).toEqual([3, 4]);
  });

  it("renders the moving marker on the curved route point instead of its coarse straight-line coordinate", async () => {
    const curvedLeg: JourneyLegSnapshot = {
      ...activeJourneySnapshot.leg,
      stations: [
        { index: 0, name: "출발", lat: 0, lon: 0 },
        { index: 1, name: "도착", lat: 0, lon: 10 },
      ],
      shape: [[0, 0], [5, 5], [0, 10]] as Coordinate[],
    };
    render(
      <LiveJourneyMap
        journeyLegKey="1:0"
        leg={curvedLeg}
        train={{ ...activeJourneySnapshot.train!, station_index: 1, status: "between", lat: 0, lon: 5 }}
      />,
    );

    await waitFor(() => expect(leaflet.marker).toHaveBeenCalledWith(
      [5, 5],
      expect.objectContaining({ icon: expect.any(Object) }),
    ));
  });

  it("mounts only through the client effect, updates one train marker, and cleans distinct map resources up", async () => {
    const { rerender, unmount } = render(
      <LiveJourneyMap journeyLegKey="1:0" leg={activeJourneySnapshot.leg} train={activeJourneySnapshot.train} />,
    );

    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(1));
    expect(leaflet.tileLayer).toHaveBeenCalledWith(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      expect.objectContaining({ maxZoom: 19 }),
    );
    expect(leaflet.polyline).toHaveBeenCalledWith(activeJourneySnapshot.leg.shape, expect.any(Object));
    expect(leaflet.divIcon).toHaveBeenCalledWith(expect.objectContaining({
      className: "live-journey-map__train-icon",
      html: '<span aria-hidden="true">🚇</span>',
      iconAnchor: [20, 20],
      iconSize: [40, 40],
    }));
    expect(leaflet.marker).toHaveBeenCalledWith(
      [37.4979, 127.0276],
      expect.objectContaining({ icon: expect.any(Object) }),
    );
    expect(leaflet.mapInstances[0].fitBounds).toHaveBeenCalled();

    const movedTrain = { ...activeJourneySnapshot.train!, lat: 37.499, lon: 127.03 };
    rerender(
      <LiveJourneyMap
        journeyLegKey="1:0"
        leg={activeJourneySnapshot.leg}
        train={movedTrain}
      />,
    );
    await waitFor(() => expect(leaflet.markerInstances[0].setLatLng).toHaveBeenCalledWith(
      positionForLiveTrain(activeJourneySnapshot.leg, movedTrain),
    ));
    expect(leaflet.marker).toHaveBeenCalledTimes(1);

    rerender(<LiveJourneyMap journeyLegKey="1:1" leg={activeJourneySnapshot.leg} train={activeJourneySnapshot.train} />);
    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(2));
    expect(leaflet.markerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.polylineInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[0].remove).toHaveBeenCalledTimes(1);

    unmount();
    expect(leaflet.markerInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.polylineInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayerInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[1].remove).toHaveBeenCalledTimes(1);
  });

  it("cleans up a partially constructed map after setup fails and can initialize a later valid leg", async () => {
    leaflet.polyline.mockImplementationOnce(() => {
      throw new Error("route layer failed");
    });
    const { rerender, unmount } = render(
      <LiveJourneyMap journeyLegKey="1:0" leg={activeJourneySnapshot.leg} train={activeJourneySnapshot.train} />,
    );

    expect(await screen.findByRole("status")).toHaveTextContent("지도를 표시하지 못했어요.");
    expect(leaflet.map).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayerInstances[0].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[0].remove).toHaveBeenCalledTimes(1);

    rerender(<LiveJourneyMap journeyLegKey="1:1" leg={activeJourneySnapshot.leg} train={activeJourneySnapshot.train} />);
    await waitFor(() => expect(leaflet.map).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("지도를 표시하지 못했어요. 이동 상태는 계속 안내할게요.")).not.toBeInTheDocument();
    expect(leaflet.tileLayerInstances[1]).not.toBe(leaflet.tileLayerInstances[0]);

    unmount();
    expect(leaflet.tileLayerInstances[1].remove).toHaveBeenCalledTimes(1);
    expect(leaflet.mapInstances[1].remove).toHaveBeenCalledTimes(1);
  });
});
