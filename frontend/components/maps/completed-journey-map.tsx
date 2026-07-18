"use client";

import { useEffect, useRef, useState } from "react";

import type { Coordinate, JourneyTrip, JourneyTripLeg } from "../../lib/types";

type CompletedJourneyMapProps = {
  trip: JourneyTrip;
  journeyKey: string;
};

type LeafletLayer = {
  addTo: (map: LeafletMap) => unknown;
  remove: () => void;
};

type LeafletMarker = LeafletLayer;

type LeafletMap = {
  fitBounds: (bounds: unknown, options: { maxZoom: number; padding: [number, number] }) => unknown;
  remove: () => void;
  setView: (position: Coordinate, zoom: number) => unknown;
};

type LeafletResource = {
  map: LeafletMap;
  markers: LeafletMarker[];
  routeLayer: LeafletLayer | null;
  tileLayer: LeafletLayer | null;
};

function isFiniteCoordinate(point: unknown): point is Coordinate {
  return Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1]);
}

function geometryForLeg(leg: Pick<JourneyTripLeg, "shape" | "stations">): Coordinate[] {
  const shape = leg.shape.filter(isFiniteCoordinate);
  if (shape.length >= 2) {
    return shape;
  }

  return leg.stations
    .filter((station) => Number.isFinite(station.lat) && Number.isFinite(station.lon))
    .map((station) => [station.lat, station.lon] as Coordinate);
}

/** Builds the complete delivered trip in leg order, including each post-leg transfer walk. */
export function geometryForCompletedTrip(trip: JourneyTrip): Coordinate[] {
  return trip.legs.flatMap((leg) => [
    ...geometryForLeg(leg),
    ...leg.transfer_walk_shape.filter(isFiniteCoordinate),
  ]);
}

function cleanupResource(resource: LeafletResource) {
  for (const marker of resource.markers) {
    try {
      marker.remove();
    } catch {
      // Continue releasing every other resource after a Leaflet cleanup error.
    }
  }
  resource.markers = [];

  try {
    resource.routeLayer?.remove();
  } catch {
    // Continue releasing every other resource after a Leaflet cleanup error.
  }
  resource.routeLayer = null;

  try {
    resource.tileLayer?.remove();
  } catch {
    // Continue releasing every other resource after a Leaflet cleanup error.
  }
  resource.tileLayer = null;

  try {
    resource.map.remove();
  } catch {
    // There are no more map-owned resources after the map instance.
  }
}

/**
 * A client/effect-only Leaflet boundary for a final trip. Leaflet and map APIs
 * are never evaluated by static generation; they load after the canvas mounts.
 */
export function CompletedJourneyMap({ trip, journeyKey }: CompletedJourneyMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const resourceRef = useRef<LeafletResource | null>(null);
  const [mapError, setMapError] = useState(false);
  const geometry = geometryForCompletedTrip(trip);
  const geometryKey = geometry.map((point) => point.join(",")).join(";");

  useEffect(() => {
    const container = containerRef.current;
    let active = true;
    setMapError(false);

    if (!container || geometry.length === 0) {
      setMapError(true);
      return undefined;
    }

    void import("leaflet")
      .then((leaflet) => {
        if (!active) {
          return;
        }

        const map = leaflet.map(container, { attributionControl: true, zoomControl: false }) as unknown as LeafletMap;
        const resource: LeafletResource = { map, markers: [], routeLayer: null, tileLayer: null };
        resourceRef.current = resource;

        try {
          resource.tileLayer = leaflet.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors",
            maxZoom: 19,
          }) as unknown as LeafletLayer;
          resource.tileLayer.addTo(map);

          resource.routeLayer = geometry.length >= 2
            ? leaflet.polyline(geometry, { color: "#183f70", opacity: 0.85, weight: 5 }) as unknown as LeafletLayer
            : null;
          resource.routeLayer?.addTo(map);

          const startMarker = leaflet.marker(geometry[0], { alt: "출발지", title: "출발지" }) as unknown as LeafletMarker;
          resource.markers.push(startMarker);
          startMarker.addTo(map);

          const endMarker = leaflet.marker(geometry[geometry.length - 1], { alt: "도착지", title: "도착지" }) as unknown as LeafletMarker;
          resource.markers.push(endMarker);
          endMarker.addTo(map);

          const bounds = leaflet.latLngBounds(geometry);
          if (bounds.isValid()) {
            map.fitBounds(bounds, { maxZoom: 15, padding: [24, 24] });
          } else {
            map.setView(geometry[0], 14);
          }
        } catch (error) {
          cleanupResource(resource);
          if (resourceRef.current === resource) {
            resourceRef.current = null;
          }
          throw error;
        }
      })
      .catch(() => {
        if (active) {
          setMapError(true);
        }
      });

    return () => {
      active = false;
      const resource = resourceRef.current;
      resourceRef.current = null;
      if (resource) {
        cleanupResource(resource);
      }
    };
  }, [geometryKey, journeyKey]);

  return (
    <section aria-labelledby="completed-map-title" className="completed-journey-map">
      <h3 id="completed-map-title">전체 이동 경로</h3>
      <div aria-label="전체 이동 경로 지도" className="completed-journey-map__canvas" ref={containerRef} role="img" />
      {mapError ? <p className="completed-journey-map__fallback" role="status">전체 경로 지도를 표시하지 못했어요. 이동 기록 상태는 계속 안내할게요.</p> : null}
    </section>
  );
}
