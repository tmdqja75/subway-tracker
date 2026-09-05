"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getNotificationConfig,
  getNotificationSubscriptionStatus,
  registerNotificationSubscription,
  unsubscribeNotification,
} from "../lib/api";
import type { WebPushConfigResponse, WebPushSubscriptionRequest } from "../lib/types";

export type NotificationSettingsPhase =
  | "checking"
  | "unsupported"
  | "server_disabled"
  | "permission_denied"
  | "disabled"
  | "subscribing"
  | "unsubscribing"
  | "enabled"
  | "error";

interface NotificationSettingsState {
  phase: NotificationSettingsPhase;
  error: string | null;
}

function hasPushCapabilities(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator.serviceWorker !== "undefined" &&
    typeof window.PushManager !== "undefined" &&
    typeof window.Notification !== "undefined"
  );
}

function urlBase64ToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

function subscriptionPayload(subscription: PushSubscription): WebPushSubscriptionRequest {
  const json = subscription.toJSON();
  const endpoint = json.endpoint ?? subscription.endpoint;
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;

  if (!endpoint || !p256dh || !auth) {
    throw new Error("브라우저 구독 정보를 읽지 못했어요.");
  }

  return { endpoint, keys: { p256dh, auth } };
}

function actionError(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "알림 설정을 변경하지 못했어요.";
}

export function useNotificationSettings() {
  const [state, setState] = useState<NotificationSettingsState>({ phase: "checking", error: null });
  const [isLoading, setIsLoading] = useState(true);
  const [reloadToken, setReloadToken] = useState(0);
  const configRef = useRef<WebPushConfigResponse | null>(null);
  const mountedRef = useRef(true);
  const operationRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      operationRef.current?.abort();
    };
  }, []);

  const retry = useCallback(() => {
    setReloadToken((current) => current + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const canUpdate = () => !controller.signal.aborted && mountedRef.current;

    const load = async () => {
      if (!hasPushCapabilities()) {
        if (canUpdate()) {
          setState({ phase: "unsupported", error: null });
          setIsLoading(false);
        }
        return;
      }

      if (canUpdate()) {
        setState({ phase: "checking", error: null });
        setIsLoading(true);
      }

      try {
        const config = await getNotificationConfig(controller.signal);
        if (!canUpdate()) return;
        configRef.current = config;
        if (!config.enabled || !config.public_key) {
          setState({ phase: "server_disabled", error: null });
          return;
        }

        const subscription = await getNotificationSubscriptionStatus(controller.signal);
        if (!canUpdate()) return;
        if (window.Notification.permission === "denied") {
          setState({ phase: "permission_denied", error: null });
          return;
        }
        setState({ phase: subscription.enabled ? "enabled" : "disabled", error: null });
      } catch (error: unknown) {
        if (!canUpdate()) return;
        setState({ phase: "error", error: actionError(error) });
      } finally {
        if (canUpdate()) {
          setIsLoading(false);
        }
      }
    };

    void load();
    return () => controller.abort();
  }, [reloadToken]);

  const enable = useCallback(async () => {
    if (!hasPushCapabilities() || state.phase === "permission_denied") return;
    const config = configRef.current;
    if (!config?.enabled || !config.public_key) {
      setState({ phase: "server_disabled", error: null });
      return;
    }

    operationRef.current?.abort();
    const controller = new AbortController();
    operationRef.current = controller;
    const canUpdate = () => !controller.signal.aborted && mountedRef.current;
    if (canUpdate()) setState({ phase: "subscribing", error: null });

    try {
      const permission = window.Notification.permission === "granted"
        ? "granted"
        : await window.Notification.requestPermission();
      if (!canUpdate()) return;
      if (permission !== "granted") {
        setState({ phase: "permission_denied", error: null });
        return;
      }

      const registration = await navigator.serviceWorker.register("/service-worker.js");
      if (!canUpdate()) return;
      const existing = await registration.pushManager.getSubscription();
      if (!canUpdate()) return;
      const browserSubscription = existing ?? await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config.public_key),
      });
      if (!canUpdate()) return;

      await registerNotificationSubscription(subscriptionPayload(browserSubscription), controller.signal);
      if (canUpdate()) setState({ phase: "enabled", error: null });
    } catch (error: unknown) {
      if (canUpdate()) setState({ phase: "error", error: actionError(error) });
    }
  }, [state.phase]);

  const disable = useCallback(async () => {
    if (!hasPushCapabilities()) return;
    operationRef.current?.abort();
    const controller = new AbortController();
    operationRef.current = controller;
    const canUpdate = () => !controller.signal.aborted && mountedRef.current;
    if (canUpdate()) setState({ phase: "unsubscribing", error: null });

    try {
      const registration = await navigator.serviceWorker.register("/service-worker.js");
      if (!canUpdate()) return;
      const browserSubscription = await registration.pushManager.getSubscription();
      if (!canUpdate()) return;
      if (!browserSubscription) {
        throw new Error("브라우저 구독 정보를 찾을 수 없어요. 다시 확인해 주세요.");
      }
      const endpoint = browserSubscription.endpoint;
      await browserSubscription.unsubscribe();
      if (!canUpdate()) return;
      await unsubscribeNotification(endpoint, controller.signal);
      if (canUpdate()) setState({ phase: "disabled", error: null });
    } catch (error: unknown) {
      if (canUpdate()) setState({ phase: "error", error: actionError(error) });
    }
  }, []);

  return { ...state, isLoading, enable, disable, retry };
}
