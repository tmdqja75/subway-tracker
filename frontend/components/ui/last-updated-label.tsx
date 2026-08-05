"use client";

import { useEffect, useState } from "react";

type LastUpdatedLabelProps = {
  updatedAt: number | null;
};

function formatElapsed(seconds: number): string {
  return seconds < 1 ? "방금 전" : `${seconds}초 전`;
}

/** Ticks every second so riders can see the backend is actually still polling their position. */
export function LastUpdatedLabel({ updatedAt }: LastUpdatedLabelProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (updatedAt === null) {
      return;
    }
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [updatedAt]);

  if (updatedAt === null) {
    return null;
  }

  const seconds = Math.max(0, Math.floor((now - updatedAt) / 1000));

  return (
    <p aria-live="polite" className="last-updated" role="status">
      <span aria-hidden="true" className="last-updated__dot" />
      마지막 업데이트: {formatElapsed(seconds)}
    </p>
  );
}
