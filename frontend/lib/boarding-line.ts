import type { ArrivingTrain, LegStation, OnboardTrain } from "./types";

const WINDOW = 3;

export type BoardingLineTrainState = "approaching" | "departed" | "arrived";

export interface BoardingLineStation {
  name: string;
  isCurrent: boolean;
}

export interface BoardingLineTrain {
  key: string;
  trainNo: string;
  destination: string;
  isExpress: boolean;
  state: BoardingLineTrainState;
  /** Index into the windowed stations array. Set for an "arrived" train, sitting on that station's node. */
  atIndex?: number;
  /** Index into the windowed stations array of the segment's earlier station. Set for "approaching"/"departed". */
  fromGapIndex?: number;
}

export interface BoardingLineData {
  stations: BoardingLineStation[];
  trains: BoardingLineTrain[];
}

/**
 * Builds the windowed line-diagram model for the boarding view: up to
 * `WINDOW` stations before/after the rider's boarding station, plus every
 * direction-matched train placed along it.
 *
 * Placement rules: an arrived train sits on its station's node; a departed
 * train sits a quarter of the way into the next segment; an approaching
 * train sits a quarter of the way before the station it's nearing.
 *
 * ponytail: ArrivingTrain only reports an integer `stations_away`, not a
 * sub-segment fraction, so each "N stations away" train is placed as
 * approaching the station N stops before the boarding station. OnboardTrain's
 * "between" status has no fraction rule from product either, so it's placed
 * at the segment midpoint. Upgrade both if the backend ever exposes finer
 * position data.
 */
export function buildBoardingLine(
  legStations: LegStation[],
  currentStationName: string,
  arrivingTrains: ArrivingTrain[],
  onboardTrains: OnboardTrain[],
): BoardingLineData | null {
  const currentIdx = legStations.findIndex((station) => station.name === currentStationName);
  if (currentIdx === -1) {
    return null;
  }

  const windowStart = Math.max(0, currentIdx - WINDOW);
  const windowEnd = Math.min(legStations.length - 1, currentIdx + WINDOW);
  const stations: BoardingLineStation[] = legStations.slice(windowStart, windowEnd + 1).map((station, i) => ({
    name: station.name,
    isCurrent: windowStart + i === currentIdx,
  }));

  const trains: BoardingLineTrain[] = [];

  arrivingTrains.forEach((train, i) => {
    if (!train.matches_direction || train.stations_away === null) {
      return;
    }
    const targetIdx = currentIdx - train.stations_away;
    const fromAbs = targetIdx - 1;
    if (fromAbs < windowStart || targetIdx > windowEnd) {
      return;
    }
    trains.push({
      key: `arriving-${train.train_no}-${i}`,
      trainNo: train.train_no,
      destination: `${train.terminus}행`,
      isExpress: train.is_express,
      state: "approaching",
      fromGapIndex: fromAbs - windowStart,
    });
  });

  onboardTrains.forEach((train, i) => {
    if (!train.matches_direction) {
      return;
    }
    const stationAbs = legStations.findIndex((station) => station.index === train.station_index);
    if (stationAbs === -1) {
      return;
    }

    const entry = {
      key: `onboard-${train.train_no}-${i}`,
      trainNo: train.train_no,
      destination: `${train.terminus}행`,
      isExpress: train.is_express,
    };

    if (train.status === "arrived") {
      if (stationAbs < windowStart || stationAbs > windowEnd) {
        return;
      }
      trains.push({ ...entry, state: "arrived", atIndex: stationAbs - windowStart });
      return;
    }

    if (train.status === "approaching") {
      const fromAbs = stationAbs - 1;
      if (fromAbs < windowStart || stationAbs > windowEnd) {
        return;
      }
      trains.push({ ...entry, state: "approaching", fromGapIndex: fromAbs - windowStart });
      return;
    }

    // "departed" and "between" both start their segment at stationAbs; the
    // component renders "between" at the same quarter-mark as "departed"
    // since there's no finer position to place it at (see doc comment above).
    if (stationAbs < windowStart || stationAbs + 1 > windowEnd) {
      return;
    }
    trains.push({ ...entry, state: "departed", fromGapIndex: stationAbs - windowStart });
  });

  return { stations, trains };
}
