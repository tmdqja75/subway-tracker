"use client";

import type { ReactNode } from "react";

import { useNotificationSettings } from "../hooks/use-notification-settings";
import { Button } from "./ui/button";

const TOGGLEABLE_PHASES = new Set(["disabled", "subscribing", "enabled", "unsubscribing"]);

export function NotificationSettings() {
  const { phase, error, isLoading, enable, disable, retry } = useNotificationSettings();

  let message = "알림 설정을 확인하고 있어요.";
  let action: ReactNode = null;
  let isError = false;
  const checked = phase === "enabled" || phase === "unsubscribing";
  const busy = phase === "subscribing" || phase === "unsubscribing";

  switch (phase) {
    case "unsupported":
      message = "이 브라우저에서는 푸시 알림을 지원하지 않아요.";
      break;
    case "server_disabled":
      message = "서버 알림 기능이 꺼져 있어요.";
      break;
    case "permission_denied":
      message = "브라우저에서 알림을 차단했어요.";
      break;
    case "disabled":
      message = "알림이 꺼져 있어요.";
      break;
    case "subscribing":
      message = "알림을 켜는 중이에요.";
      break;
    case "unsubscribing":
      message = "알림을 끄는 중이에요.";
      break;
    case "enabled":
      message = "알림이 켜져 있어요.";
      break;
    case "error":
      message = "알림 설정을 확인하지 못했어요.";
      isError = true;
      action = <Button className="notification-settings__button" onClick={retry} variant="secondary">다시 확인</Button>;
      break;
    case "checking":
      message = isLoading ? "알림 설정을 확인하고 있어요." : message;
      break;
  }

  const toggle = TOGGLEABLE_PHASES.has(phase) ? (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label="도착 알림"
      disabled={busy}
      className="notification-settings__toggle"
      data-checked={checked}
      onClick={() => void (checked ? disable() : enable())}
    >
      <span className="notification-settings__toggle-thumb" aria-hidden="true" />
    </button>
  ) : null;

  return (
    <section aria-label="도착 알림 설정" className="notification-settings">
      <div className="notification-settings__row">
        <p className="notification-settings__title">도착 알림</p>
        {toggle}
      </div>
      <div aria-live="polite" className="notification-settings__content" role={isError ? "alert" : "status"}>
        <p>{message}</p>
        {error && isError ? <p className="notification-settings__error-detail">{error}</p> : null}
      </div>
      {action}
    </section>
  );
}
