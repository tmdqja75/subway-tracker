import type {
  ActiveJourneySnapshot,
  ArrivingTrain,
  CurrentArrivalsResponse,
  IdleJourneySnapshot,
  Itinerary,
  Station,
  TrackPoint,
  TrainStatus,
} from "../lib/types";

export const station: Station = {
  station_id: "0222",
  name: "강남",
  line: "2호선",
  lat: 37.4979,
  lon: 127.0276,
};

export const itinerary: Itinerary = {
  total_time: 420,
  transfer_count: 0,
  total_walk_time: 0,
  fare: 1400,
  summary: ["강남에서 2호선 탑승"],
  legs: [
    {
      route: "수도권2호선",
      line_key: "2",
      section_time: 420,
      start_name: "강남",
      end_name: "역삼",
      stations: [
        { index: 0, name: "강남", lat: 37.4979, lon: 127.0276 },
        { index: 1, name: "역삼", lat: 37.5006, lon: 127.0364 },
      ],
      shape: [
        [37.4979, 127.0276],
        [37.5006, 127.0364],
      ],
      transfer_walk_shape: [],
      transfer_walk_time: 0,
    },
  ],
};

export const arrivingTrain: ArrivingTrain = {
  train_no: "2221",
  line_name: "2호선",
  terminus: "성수",
  direction_label: "성수행 - 구의방면",
  eta_seconds: 90,
  arrival_msg: "1분 30초 후 도착",
  stations_away: 1,
  stations_away_estimated: false,
  matches_direction: true,
  is_express: false,
};

export const trainStatus: TrainStatus = {
  train_no: "2221",
  station_name: "강남",
  station_index: 0,
  status: "departed",
  lat: 37.4979,
  lon: 127.0276,
  updated_at: 1_783_000_000,
};

export const trackPoint: TrackPoint = {
  lat: 37.499,
  lon: 127.03,
  ts: 1_783_000_030,
  estimated: false,
};

export const idleJourneySnapshot: IdleJourneySnapshot = { state: "idle" };

export const activeJourneySnapshot = {
  journey_id: 1,
  state: "on_train",
  leg_idx: 0,
  leg_count: 1,
  leg: {
    route: "수도권2호선",
    line_key: "2",
    start: "강남",
    end: "역삼",
    covered: true,
    stations: itinerary.legs[0].stations,
    shape: itinerary.legs[0].shape,
    transfer_walk_shape: [],
    transfer_walk_time: 0,
  },
  summary: itinerary.summary,
  train: trainStatus,
  tracking_mode: "realtime",
  history_estimated: false,
  point_count: 1,
  error: null,
  transfer: null,
  trip: {
    legs: [
      {
        route: "수도권2호선",
        start: "강남",
        end: "역삼",
        stations: itinerary.legs[0].stations,
        shape: itinerary.legs[0].shape,
        transfer_walk_shape: [],
      },
    ],
  },
} satisfies ActiveJourneySnapshot;

export const pushFailedJourneySnapshot = {
  ...activeJourneySnapshot,
  state: "push_failed",
  error: "upstream timed out",
  transfer: {
    sent_points: 3,
    total_points: 5,
    remaining_points: 2,
    progress_percent: 60,
    reason: "network",
    message: "전송 중 네트워크 오류가 발생했어요.",
    detail: "upstream timed out",
    can_retry: true,
  },
} satisfies ActiveJourneySnapshot;

export const arrivalsResponse: CurrentArrivalsResponse = {
  covered: true,
  trains: [arrivingTrain],
  already_onboard: [],
};
