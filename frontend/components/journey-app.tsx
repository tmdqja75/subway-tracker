"use client";

import { useEffect, useState, type CSSProperties } from "react";

import { useCurrentJourney } from "../hooks/use-current-journey";
import { getLineColor } from "../lib/line-colors";
import type { CurrentJourneyResponse, Itinerary } from "../lib/types";
import { NotificationSettings } from "./notification-settings";
import { JourneySearch } from "./journey-search";
import { LiveJourney } from "./live-journey";
import { JourneyStepper, type JourneyStep } from "./journey-stepper";
import { RouteList } from "./route-list";
import { TrainPicker } from "./train-picker";
import { TransferStatus } from "./transfer-status";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { StatusBanner } from "./ui/status-banner";

function stepFor(snapshot: CurrentJourneyResponse | null, hasRoutes: boolean): JourneyStep {
  if (snapshot === null || snapshot.state === "idle") {
    return hasRoutes ? "경로" : "검색";
  }

  switch (snapshot.state) {
    case "awaiting_board":
      return "탑승";
    case "on_train":
      return "이동";
    case "pushing":
    case "completed":
    case "push_failed":
      return "완료";
  }
}

function activeStateLabel(snapshot: Exclude<CurrentJourneyResponse, { state: "idle" }>): string {
  switch (snapshot.state) {
    case "on_train":
      return "열차 이동";
    case "pushing":
      return "이동 기록 전송";
    case "completed":
      return "여정 완료";
    case "push_failed":
      return "전송 재시도 대기";
    case "awaiting_board":
      return "탑승 열차 선택";
  }
}

export function JourneyApp() {
  const [routes, setRoutes] = useState<Itinerary[] | null>(null);
  const [startingNextJourney, setStartingNextJourney] = useState(false);
  const { snapshot, error, lastUpdatedAt, refresh } = useCurrentJourney();
  const isIdle = snapshot?.state === "idle";
  const isStartingNextJourneyFromTerminal = (snapshot?.state === "completed" || snapshot?.state === "push_failed") && startingNextJourney;
  const showsSearchFlow = isIdle || isStartingNextJourneyFromTerminal;
  const activeStep = isStartingNextJourneyFromTerminal
    ? routes ? "경로" : "검색"
    : stepFor(snapshot, isIdle && routes !== null);
  const activeLeg = !showsSearchFlow && snapshot !== null ? snapshot.leg : null;

  useEffect(() => {
    if (snapshot?.state !== "completed" && snapshot?.state !== "push_failed") {
      setStartingNextJourney(false);
    }
  }, [snapshot?.state]);

  const showStationSelection = () => {
    setRoutes(null);
    setStartingNextJourney(true);
  };

  const title = snapshot === null
    ? "여정 상태를 확인하고 있어요"
    : showsSearchFlow
      ? routes
        ? "어떤 경로로 이동할까요?"
        : "어디로 이동하세요?"
      : snapshot.state === "awaiting_board"
        ? "탑승할 열차를 선택하세요"
        : "진행 중인 여정을 복구하고 있어요";
  const chip = snapshot === null
    ? "여정 확인"
    : showsSearchFlow
      ? routes
        ? "경로 선택"
        : "경로 검색"
      : activeStateLabel(snapshot);

  return (
    <main aria-label="Subway Tracker 이동 화면" className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">SUBWAY TRACKER</p>
          <h1>도시를 따라, 더 편안하게.</h1>
        </div>
        <div className="app-header__controls">
          <span aria-label="서비스 준비됨" className="availability-indicator">
            <span aria-hidden="true" />
            준비됨
          </span>
          <NotificationSettings />
        </div>
      </header>

      <JourneyStepper activeStep={activeStep} />

      <StatusBanner title={showsSearchFlow ? "이동을 시작할 준비가 됐어요." : "저장된 여정 상태를 확인하고 있어요."} tone="success">
        {showsSearchFlow
          ? routes
            ? "원하는 경로를 선택하면 탑승 안내를 시작할게요."
            : "출발역과 도착역을 정하면 실시간 이동 흐름을 안내해 드릴게요."
          : "새로고침해도 백엔드의 현재 여정 상태를 기준으로 이어집니다."}
      </StatusBanner>

      {error ? (
        <div className="journey-sync-error" role="alert">
          <p>{error}</p>
          <Button onClick={refresh} variant="secondary">다시 시도</Button>
        </div>
      ) : null}

      <Card
        aria-labelledby="journey-workflow-title"
        className="journey-card"
        style={activeLeg ? ({ "--line-color": getLineColor(activeLeg.line_key) } as CSSProperties) : undefined}
      >
        <div className="journey-card__header">
          <div>
            <p className="eyebrow">YOUR JOURNEY</p>
            <h2 id="journey-workflow-title">{title}</h2>
          </div>
          <span className="idle-chip">{chip}</span>
        </div>
        {activeLeg ? <p className="line-plate">{activeLeg.line_key ?? "노선 정보 없음"}</p> : null}

        {snapshot === null ? (
          <div className="journey-handoff" role="status">
            <strong>현재 여정 정보를 기다리고 있어요.</strong>
            <p>확인이 끝날 때까지 새로운 경로 검색은 시작하지 않습니다.</p>
          </div>
        ) : showsSearchFlow ? (
          routes ? (
            <RouteList
              itineraries={routes}
              onBack={() => setRoutes(null)}
              onStarted={refresh}
            />
          ) : (
            <JourneySearch onRoutes={setRoutes} />
          )
        ) : snapshot.state === "awaiting_board" ? (
          <TrainPicker journey={snapshot} onJourneyRefresh={refresh} />
        ) : snapshot.state === "on_train" ? (
          <LiveJourney journey={snapshot} lastUpdatedAt={lastUpdatedAt} onJourneyRefresh={refresh} />
        ) : snapshot.state === "completed" ? (
          <TransferStatus
            journey={snapshot}
            onBeginNextJourney={showStationSelection}
            onJourneyRefresh={refresh}
          />
        ) : snapshot.state === "pushing" || snapshot.state === "push_failed" ? (
          <TransferStatus
            journey={snapshot}
            onDeferPush={snapshot.state === "push_failed" ? showStationSelection : undefined}
            onJourneyRefresh={refresh}
          />
        ) : null}
      </Card>

      <section aria-labelledby="experience-title" className="experience-notes">
        <p className="eyebrow">RIDE WITH CLARITY</p>
        <h2 id="experience-title">필요한 순간에, 필요한 정보만.</h2>
        <ul>
          <li>
            <span aria-hidden="true" className="note-icon note-icon--navy" />
            <div>
              <strong>한눈에 보는 경로</strong>
              <p>환승과 이동 단계를 차분하게 정리합니다.</p>
            </div>
          </li>
          <li>
            <span aria-hidden="true" className="note-icon note-icon--green" />
            <div>
              <strong>탑승 뒤의 실시간 안내</strong>
              <p>여정이 시작되면 현재 흐름을 이어서 보여줍니다.</p>
            </div>
          </li>
        </ul>
      </section>
    </main>
  );
}
