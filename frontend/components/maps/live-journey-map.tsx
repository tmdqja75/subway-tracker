"use client";

import { useEffect, useRef, useState } from "react";

import type { Coordinate, JourneyLegSnapshot, TrainStatus } from "../../lib/types";

type LiveJourneyMapProps = {
  leg: JourneyLegSnapshot;
  train: TrainStatus | null;
  journeyLegKey?: string;
};

type TrainMarker = {
  addTo: (map: any) => unknown;
  remove: () => void;
  setLatLng: (latLng: Coordinate) => unknown;
};

type LeafletIcon = import("leaflet").DivIcon;
type LeafletMarkerOptions = import("leaflet").MarkerOptions;

type LeafletLayer = {
  addTo: (map: any) => unknown;
  remove: () => void;
};

type LeafletResource = {
  leaflet: {
    divIcon: (options: {
      className: string;
      html: string;
      iconAnchor: [number, number];
      iconSize: [number, number];
    }) => LeafletIcon;
    marker: (position: Coordinate, options: LeafletMarkerOptions) => TrainMarker;
  };
  map: { remove: () => void };
  routeLayer: LeafletLayer | null;
  tileLayer: LeafletLayer | null;
  trainMarker: TrainMarker | null;
};

function isFiniteCoordinate(point: unknown): point is Coordinate {
  return Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1]);
}

/** Uses valid Tmap route geometry when present, with station coordinates as a safe fallback. */
export function geometryForLiveLeg(leg: Pick<JourneyLegSnapshot, "shape" | "stations">): Coordinate[] {
  const shape = leg.shape.filter(isFiniteCoordinate);
  if (shape.length >= 2) {
    return shape;
  }

  return leg.stations
    .filter((station) => Number.isFinite(station.lat) && Number.isFinite(station.lon))
    .map((station) => [station.lat, station.lon] as Coordinate);
}

function distanceMeters(a: Coordinate, b: Coordinate): number {
  const earthRadiusMeters = 6_371_000;
  const toRadians = (degrees: number) => degrees * Math.PI / 180;
  const dLat = toRadians(b[0] - a[0]);
  const dLon = toRadians(b[1] - a[1]);
  const lat1 = toRadians(a[0]);
  const lat2 = toRadians(b[0]);
  const chord = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * earthRadiusMeters * Math.atan2(Math.sqrt(chord), Math.sqrt(1 - chord));
}

function closestShapeIndex(geometry: Coordinate[], station: Coordinate, fromIndex = 0): number {
  let closestIndex = fromIndex;
  let closestDistance = Number.POSITIVE_INFINITY;
  for (let index = fromIndex; index < geometry.length; index += 1) {
    const point = geometry[index];
    const distance = (point[0] - station[0]) ** 2 + (point[1] - station[1]) ** 2;
    if (distance < closestDistance) {
      closestDistance = distance;
      closestIndex = index;
    }
  }
  return closestIndex;
}

function pointAtDistanceFraction(geometry: Coordinate[], fraction: number): Coordinate {
  const lengths = geometry.slice(1).map((point, index) => distanceMeters(geometry[index], point));
  const totalLength = lengths.reduce((total, length) => total + length, 0);
  if (totalLength <= 0) {
    return geometry[0];
  }

  const targetDistance = totalLength * Math.min(Math.max(fraction, 0), 1);
  let traversed = 0;
  for (let index = 0; index < lengths.length; index += 1) {
    const length = lengths[index];
    if (traversed + length >= targetDistance) {
      const segmentFraction = length === 0 ? 0 : (targetDistance - traversed) / length;
      const start = geometry[index];
      const end = geometry[index + 1];
      return [
        start[0] + (end[0] - start[0]) * segmentFraction,
        start[1] + (end[1] - start[1]) * segmentFraction,
      ];
    }
    traversed += length;
  }
  return geometry[geometry.length - 1];
}

/**
 * Seoul's feed reports station-relative state, while the backend provides a
 * coarse straight-line interpolation. Reproject that progress over Tmap's
 * route shape so the marker follows the visible rail curve without implying
 * GPS-level accuracy.
 */
export function positionForLiveTrain(
  leg: Pick<JourneyLegSnapshot, "shape" | "stations">,
  train: TrainStatus,
): Coordinate {
  const fallback: Coordinate = [train.lat, train.lon];
  if (!Number.isFinite(train.lat) || !Number.isFinite(train.lon) || train.station_index === null) {
    return fallback;
  }

  const startStationIndex = train.status === "between"
    // Older local snapshots labelled a first-segment interpolation as
    // "between" while retaining index 0. Clamp that legacy form to the
    // first actual segment rather than reverting to the straight-line point.
    ? Math.max(train.station_index - 1, 0)
    : train.status === "departed" || train.status === "estimated"
      ? train.station_index
      : null;
  if (startStationIndex === null || startStationIndex < 0 || startStationIndex >= leg.stations.length - 1) {
    return fallback;
  }

  const geometry = leg.shape.filter(isFiniteCoordinate);
  if (geometry.length < 2) {
    return fallback;
  }

  const startStation = leg.stations[startStationIndex];
  const endStation = leg.stations[startStationIndex + 1];
  const start: Coordinate = [startStation.lat, startStation.lon];
  const end: Coordinate = [endStation.lat, endStation.lon];
  const segmentLat = end[0] - start[0];
  const segmentLon = end[1] - start[1];
  const segmentLengthSquared = segmentLat ** 2 + segmentLon ** 2;
  if (segmentLengthSquared === 0) {
    return fallback;
  }

  const progress = ((train.lat - start[0]) * segmentLat + (train.lon - start[1]) * segmentLon) / segmentLengthSquared;
  const shapeStartIndex = closestShapeIndex(geometry, start);
  const shapeEndIndex = closestShapeIndex(geometry, end, shapeStartIndex);
  if (shapeEndIndex <= shapeStartIndex) {
    return fallback;
  }
  return pointAtDistanceFraction(geometry.slice(shapeStartIndex, shapeEndIndex + 1), progress);
}

function hasTrainPosition(train: TrainStatus | null): train is TrainStatus {
  return train !== null && Number.isFinite(train.lat) && Number.isFinite(train.lon);
}

function syncTrainMarker(
  resource: LeafletResource,
  leg: Pick<JourneyLegSnapshot, "shape" | "stations">,
  train: TrainStatus | null,
) {
  if (!hasTrainPosition(train)) {
    resource.trainMarker?.remove();
    resource.trainMarker = null;
    return;
  }

  const position = positionForLiveTrain(leg, train);
  if (resource.trainMarker) {
    resource.trainMarker.setLatLng(position);
    return;
  }

  const icon = resource.leaflet.divIcon({
    className: "live-journey-map__train-icon",
    html: '<span aria-hidden="true">🚇</span>',
    iconAnchor: [20, 20],
    iconSize: [40, 40],
  });
  resource.trainMarker = resource.leaflet.marker(position, {
    alt: `${train.train_no} 열차 위치`,
    icon,
    title: `${train.train_no} 열차`,
  });
  resource.trainMarker.addTo(resource.map);
}

function cleanupResource(resource: LeafletResource) {
  try {
    resource.trainMarker?.remove();
  } catch {
    // Finish releasing the rest of the map resources even when a Leaflet cleanup fails.
  }
  resource.trainMarker = null;

  try {
    resource.routeLayer?.remove();
  } catch {
    // Finish releasing the rest of the map resources even when a Leaflet cleanup fails.
  }
  resource.routeLayer = null;

  try {
    resource.tileLayer?.remove();
  } catch {
    // Finish releasing the rest of the map resources even when a Leaflet cleanup fails.
  }
  resource.tileLayer = null;

  try {
    resource.map.remove();
  } catch {
    // There is nothing else to release after the map instance.
  }
}

/**
 * A client/effect-only Leaflet boundary. Static generation only receives this
 * inert container; Leaflet itself is loaded after the browser has mounted it.
 */
export function LiveJourneyMap({ leg, train, journeyLegKey = "" }: LiveJourneyMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const resourceRef = useRef<LeafletResource | null>(null);
  const trainRef = useRef(train);
  const [mapError, setMapError] = useState(false);
  const geometry = geometryForLiveLeg(leg);
  const legKey = `${leg.route}:${leg.start}:${leg.end}:${geometry.map((point) => point.join(",")).join(";")}`;

  trainRef.current = train;

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

        const map = leaflet.map(container, { attributionControl: true, zoomControl: false });
        const resource: LeafletResource = {
          leaflet,
          map,
          routeLayer: null,
          tileLayer: null,
          trainMarker: null,
        };
        resourceRef.current = resource;

        try {
          resource.tileLayer = leaflet.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors",
            maxZoom: 19,
          });
          resource.tileLayer.addTo(map);
          resource.routeLayer = geometry.length >= 2
            ? leaflet.polyline(geometry, { color: "#183f70", opacity: 0.85, weight: 5 })
            : null;
          resource.routeLayer?.addTo(map);
          const bounds = leaflet.latLngBounds(geometry);

          if (bounds.isValid()) {
            map.fitBounds(bounds, { maxZoom: 15, padding: [24, 24] });
          } else {
            map.setView(geometry[0], 14);
          }

          syncTrainMarker(resource, leg, trainRef.current);
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
  }, [journeyLegKey, legKey]);

  useEffect(() => {
    const resource = resourceRef.current;
    if (resource) {
      syncTrainMarker(resource, leg, train);
    }
  }, [leg, train]);

  return (
    <section aria-labelledby="live-map-title" className="live-journey-map">
      <h3 id="live-map-title">현재 이동 경로</h3>
      <div
        aria-label="현재 이동 경로 지도"
        className="live-journey-map__canvas"
        ref={containerRef}
        role="img"
      />
      {mapError ? <p className="live-journey-map__fallback" role="status">지도를 표시하지 못했어요. 이동 상태는 계속 안내할게요.</p> : null}
    </section>
  );
}
