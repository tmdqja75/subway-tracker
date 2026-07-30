import type { BoardingLineData, BoardingLineTrain } from "../lib/boarding-line";

function groupBy<T>(items: T[], key: (item: T) => number | undefined): Map<number, T[]> {
  const groups = new Map<number, T[]>();
  for (const item of items) {
    const k = key(item);
    if (k === undefined) {
      continue;
    }
    const list = groups.get(k) ?? [];
    list.push(item);
    groups.set(k, list);
  }
  return groups;
}

function trainTop(train: BoardingLineTrain): string | undefined {
  if (train.atIndex !== undefined) {
    return undefined;
  }
  return train.state === "departed" ? "25%" : "75%";
}

function TrainIcon({ train }: { train: BoardingLineTrain }) {
  const top = trainTop(train);
  return (
    <span
      className={`boarding-line__train-icon boarding-line__train-icon--${train.state}`}
      style={top ? { top } : undefined}
    >
      🚇
    </span>
  );
}

function TrainPill({ train }: { train: BoardingLineTrain }) {
  const top = trainTop(train);
  return (
    <span
      className={`boarding-line__train-pill boarding-line__train-pill--${train.state}`}
      style={top ? { top } : undefined}
    >
      {train.trainNo}
      <span className="boarding-line__dest">{train.destination}</span>
      {train.isExpress ? <span className="boarding-line__express">급행</span> : null}
    </span>
  );
}

/**
 * Decorative summary of nearby trains along the boarding station's line.
 * The interactive, screen-reader-facing train list lives in TrainPicker;
 * this is a visual duplicate of that same data, so it's hidden from AT.
 */
export function BoardingLine({ stations, trains }: BoardingLineData) {
  if (stations.length === 0) {
    return null;
  }

  const trainsByStation = groupBy(trains, (train) => train.atIndex);
  const trainsByGap = groupBy(trains, (train) => train.fromGapIndex);

  return (
    <div aria-hidden="true" className="boarding-line">
      {stations.map((station, index) => {
        const stationTrains = trainsByStation.get(index) ?? [];
        return (
          <div key={`${station.name}-${index}`}>
            <div
              className={`boarding-line__station${station.isCurrent ? " boarding-line__station--current" : ""}`}
            >
              <span className="boarding-line__name">{station.name}</span>
              <span className="boarding-line__rail-col">
                <span className="boarding-line__node" />
                {stationTrains.map((train) => <TrainIcon key={train.key} train={train} />)}
              </span>
              <span className="boarding-line__detail">
                {station.isCurrent ? <span className="boarding-line__here-chip">현재 위치</span> : null}
                {stationTrains.map((train) => <TrainPill key={train.key} train={train} />)}
              </span>
            </div>

            {index < stations.length - 1 ? (
              <div className="boarding-line__gap">
                <span className="boarding-line__name-spacer" />
                <span className="boarding-line__rail-col">
                  {(trainsByGap.get(index) ?? []).map((train) => <TrainIcon key={train.key} train={train} />)}
                </span>
                <span className="boarding-line__detail">
                  {(trainsByGap.get(index) ?? []).map((train) => <TrainPill key={train.key} train={train} />)}
                </span>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
