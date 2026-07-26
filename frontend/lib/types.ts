/** Backend API payload contracts. Coordinates are always [latitude, longitude]. */

export type Coordinate = [lat: number, lon: number];

export interface Station {
  station_id: string;
  name: string;
  line: string;
  lat: number;
  lon: number;
}

export interface RouteHistoryItem {
  start: Station;
  end: Station;
}

export interface RouteHistoryResponse {
  most_used: RouteHistoryItem[];
  recent: RouteHistoryItem[];
}

export interface LegStation {
  index: number;
  name: string;
  lat: number;
  lon: number;
}

export interface SubwayLeg {
  route: string;
  line_key: string | null;
  section_time: number;
  start_name: string;
  end_name: string;
  stations: LegStation[];
  shape: Coordinate[];
  transfer_walk_shape: Coordinate[];
  transfer_walk_time: number;
}

export interface Itinerary {
  total_time: number;
  transfer_count: number;
  total_walk_time: number;
  fare: number | null;
  legs: SubwayLeg[];
  summary: string[];
}

export interface ArrivingTrain {
  train_no: string;
  line_name: string;
  terminus: string;
  direction_label: string;
  eta_seconds: number;
  arrival_msg: string;
  stations_away: number | null;
  stations_away_estimated: boolean;
  matches_direction: boolean;
  is_express: boolean;
}

export type OnboardTrainStatus = "approaching" | "arrived" | "departed" | "between";

export interface OnboardTrain {
  train_no: string;
  line_name: string;
  terminus: string;
  direction_label: string;
  station_name: string;
  station_index: number;
  status: OnboardTrainStatus;
  observed_at: number;
  matches_direction: boolean;
  is_express: boolean;
}

export interface TrackPoint {
  lat: number;
  lon: number;
  ts: number;
  estimated: boolean;
}

export interface TrainStatus {
  train_no: string;
  station_name: string;
  station_index: number | null; // Next station while status is "between".
  status: string;
  lat: number;
  lon: number;
  updated_at: number;
}

export type JourneyState =
  | "awaiting_board"
  | "on_train"
  | "pushing"
  | "completed"
  | "push_failed"
  | "cancelled";

export type ActiveJourneyState = Exclude<JourneyState, "cancelled">;
export type TrackingMode = "realtime" | "timer";

export interface JourneyLegSnapshot {
  route: string;
  line_key: string | null;
  start: string;
  end: string;
  covered: boolean;
  stations: LegStation[];
  shape: Coordinate[];
  transfer_walk_shape: Coordinate[];
  transfer_walk_time: number;
}

export interface JourneyTripLeg {
  route: string;
  start: string;
  end: string;
  stations: LegStation[];
  shape: Coordinate[];
  transfer_walk_shape: Coordinate[];
}

export interface JourneyTrip {
  legs: JourneyTripLeg[];
}

export interface TransferProgress {
  sent_points: number;
  total_points: number;
  remaining_points: number;
  progress_percent: number;
}

export interface PushFailureTransfer extends TransferProgress {
  reason: string;
  message: string;
  detail: string;
  can_retry: true;
}

export type JourneyTransfer = TransferProgress | PushFailureTransfer;

interface ActiveJourneySnapshotBase {
  journey_id: number;
  leg_idx: number;
  leg_count: number;
  leg: JourneyLegSnapshot;
  summary: string[];
  train: TrainStatus | null;
  tracking_mode: TrackingMode | null;
  history_estimated: boolean;
  point_count: number;
  error: string | null;
  trip: JourneyTrip;
}

export type ActiveJourneySnapshot =
  | (ActiveJourneySnapshotBase & { state: "awaiting_board"; transfer: null })
  | (ActiveJourneySnapshotBase & { state: "on_train"; transfer: null })
  | (ActiveJourneySnapshotBase & { state: "pushing"; transfer: TransferProgress })
  | (ActiveJourneySnapshotBase & { state: "completed"; transfer: TransferProgress })
  | (ActiveJourneySnapshotBase & { state: "push_failed"; transfer: PushFailureTransfer });

export interface IdleJourneySnapshot {
  state: "idle";
}

export type CurrentJourneyResponse = ActiveJourneySnapshot | IdleJourneySnapshot;

export interface RouteSearchRequest {
  start: string;
  end: string;
  start_id?: string | null;
  end_id?: string | null;
}

export interface StartJourneyRequest {
  itinerary: Itinerary;
}

export interface BoardJourneyRequest {
  train_no: string | null;
  retroactive: boolean;
}

export interface JourneyStartedResponse {
  journey_id: number;
  state: JourneyState;
}

export interface CurrentArrivalsResponse {
  covered: boolean;
  trains: ArrivingTrain[];
  already_onboard: OnboardTrain[];
}

export interface OkResponse {
  ok: true;
}

export interface FastApiValidationError {
  type: string;
  loc: Array<string | number>;
  msg: string;
  input: unknown;
  ctx?: Record<string, unknown>;
}

export interface ApiErrorResponse {
  detail: string | FastApiValidationError[];
}
