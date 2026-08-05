"use client";

import { useEffect, useState } from "react";

type PollCountdownRingProps = {
  /** Epoch ms the next poll fires at, or null while a fetch is in flight. */
  nextPollAt: number | null;
  durationMs: number;
};

const RADIUS = 15;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** Ring shrinks from full to empty as the wait between arrival polls ticks down. */
export function PollCountdownRing({ nextPollAt, durationMs }: PollCountdownRingProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (nextPollAt === null) {
      return;
    }
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [nextPollAt]);

  if (nextPollAt === null) {
    return (
      <span aria-label="도착 열차 정보를 확인하는 중이에요" className="poll-ring poll-ring--busy" role="status">
        <svg aria-hidden="true" height="36" viewBox="0 0 36 36" width="36">
          <circle className="poll-ring__track" cx="18" cy="18" r={RADIUS} />
        </svg>
      </span>
    );
  }

  const remainingMs = Math.max(0, nextPollAt - now);
  const remainingSeconds = Math.ceil(remainingMs / 1000);
  const fraction = Math.min(1, remainingMs / durationMs);
  const offset = CIRCUMFERENCE * (1 - fraction);

  return (
    <span aria-label={`다음 새로고침까지 ${remainingSeconds}초`} className="poll-ring" role="status">
      <svg aria-hidden="true" height="36" viewBox="0 0 36 36" width="36">
        <circle className="poll-ring__track" cx="18" cy="18" r={RADIUS} />
        <circle
          className="poll-ring__progress"
          cx="18"
          cy="18"
          r={RADIUS}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
        />
      </svg>
      <span aria-hidden="true" className="poll-ring__value">{remainingSeconds}</span>
    </span>
  );
}
