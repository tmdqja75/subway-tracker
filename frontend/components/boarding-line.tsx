import type { BoardingLineData, BoardingLineTrain } from "../lib/boarding-line";
import { TRAIN_ICON_SVG } from "../lib/train-icon";

const STATE_LABEL: Record<BoardingLineTrain["state"], string> = {
  approaching: "접근 중",
  departed: "출발함",
  arrived: "도착",
};

type BoardingLineProps = BoardingLineData & {
  disabled: boolean;
  onSelect: (trainNo: string, retroactive: boolean) => void;
};

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

function trainLabel(train: BoardingLineTrain): string {
  const parts = [`${train.trainNo} 열차`, train.destination, STATE_LABEL[train.state]];
  if (train.isExpress) {
    parts.push("급행");
  }
  if (train.retroactive) {
    parts.push("이미 탑승 중일 수 있음");
  }
  return parts.join(" · ");
}

function TrainIcon({ train }: { train: BoardingLineTrain }) {
  const top = trainTop(train);
  return (
    <span
      aria-hidden="true"
      className={`boarding-line__train-icon boarding-line__train-icon--${train.state}`}
      dangerouslySetInnerHTML={{ __html: TRAIN_ICON_SVG }}
      style={top ? { top } : undefined}
    />
  );
}

function TrainButton({
  disabled,
  onSelect,
  train,
}: {
  disabled: boolean;
  onSelect: (trainNo: string, retroactive: boolean) => void;
  train: BoardingLineTrain;
}) {
  const top = trainTop(train);
  return (
    <button
      aria-label={trainLabel(train)}
      className={`boarding-line__train-pill boarding-line__train-pill--${train.state}`}
      disabled={disabled}
      onClick={() => onSelect(train.trainNo, train.retroactive)}
      style={top ? { top } : undefined}
      type="button"
    >
      {train.trainNo}
      <span aria-hidden="true" className="boarding-line__dest">{train.destination}</span>
      {train.isExpress ? <span aria-hidden="true" className="boarding-line__express">급행</span> : null}
    </button>
  );
}

/**
 * Line diagram of the boarding station and up to 3 stations before/after it.
 * Every visible train (approaching, already departed, or arrived) is a
 * tappable button that boards it directly — the only way to pick a train.
 */
export function BoardingLine({ disabled, onSelect, stations, trains }: BoardingLineProps) {
  if (stations.length === 0) {
    return null;
  }

  const trainsByStation = groupBy(trains, (train) => train.atIndex);
  const trainsByGap = groupBy(trains, (train) => train.fromGapIndex);

  return (
    <div className="boarding-line" role="list">
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
                {stationTrains.map((train) => (
                  <span key={train.key} role="listitem">
                    <TrainButton disabled={disabled} onSelect={onSelect} train={train} />
                  </span>
                ))}
              </span>
            </div>

            {index < stations.length - 1 ? (
              <div className="boarding-line__gap">
                <span className="boarding-line__name-spacer" />
                <span className="boarding-line__rail-col">
                  {(trainsByGap.get(index) ?? []).map((train) => <TrainIcon key={train.key} train={train} />)}
                </span>
                <span className="boarding-line__detail">
                  {(trainsByGap.get(index) ?? []).map((train) => (
                    <span key={train.key} role="listitem">
                      <TrainButton disabled={disabled} onSelect={onSelect} train={train} />
                    </span>
                  ))}
                </span>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
