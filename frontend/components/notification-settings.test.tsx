import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getNotificationConfig,
  getNotificationSubscriptionStatus,
  registerNotificationSubscription,
  unsubscribeNotification,
} from "../lib/api";
import { NotificationSettings } from "./notification-settings";

vi.mock("../lib/api", () => ({
  getNotificationConfig: vi.fn(),
  getNotificationSubscriptionStatus: vi.fn(),
  registerNotificationSubscription: vi.fn(),
  unsubscribeNotification: vi.fn(),
}));

const publicKey = "BElongVapidKey";

function installPushSupport(permission: NotificationPermission = "default") {
  const subscription = {
    endpoint: "https://push.example/current",
    toJSON: vi.fn(() => ({
      endpoint: "https://push.example/current",
      keys: { p256dh: "browser-public-key", auth: "browser-auth" },
    })),
    unsubscribe: vi.fn().mockResolvedValue(true),
  };
  const registration = {
    pushManager: {
      getSubscription: vi.fn().mockResolvedValue(null),
      subscribe: vi.fn().mockResolvedValue(subscription),
    },
  };
  const requestPermission = vi.fn().mockResolvedValue("granted");
  Object.defineProperty(window.navigator, "serviceWorker", {
    configurable: true,
    value: { register: vi.fn().mockResolvedValue(registration) },
  });
  Object.defineProperty(window, "PushManager", { configurable: true, value: class PushManager {} });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { permission, requestPermission },
  });
  return { registration, requestPermission, subscription };
}

function mockServer(
  config: { enabled: boolean; public_key: string | null } = { enabled: true, public_key: publicKey },
  status = { enabled: false },
) {
  vi.mocked(getNotificationConfig).mockResolvedValue(config);
  vi.mocked(getNotificationSubscriptionStatus).mockResolvedValue(status);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  Object.defineProperty(window.navigator, "serviceWorker", { configurable: true, value: undefined });
  Object.defineProperty(window, "PushManager", { configurable: true, value: undefined });
  Object.defineProperty(window, "Notification", { configurable: true, value: undefined });
});

describe("NotificationSettings", () => {
  it("requests permission only after an explicit click, then registers, subscribes, and posts the exact payload", async () => {
    const { registration, requestPermission } = installPushSupport();
    mockServer();
    vi.mocked(registerNotificationSubscription).mockResolvedValue({ enabled: true });

    render(<NotificationSettings />);
    const toggle = await screen.findByRole("switch", { name: "도착 알림" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(requestPermission).not.toHaveBeenCalled();

    fireEvent.click(toggle);

    await waitFor(() => expect(screen.getByText("알림이 켜져 있어요.")).toBeVisible());
    expect(screen.getByRole("switch", { name: "도착 알림" })).toHaveAttribute("aria-checked", "true");
    expect(window.navigator.serviceWorker.register).toHaveBeenCalledWith("/service-worker.js");
    expect(registration.pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    });
    expect(registerNotificationSubscription).toHaveBeenCalledWith(
      {
        endpoint: "https://push.example/current",
        keys: { p256dh: "browser-public-key", auth: "browser-auth" },
      },
      expect.any(AbortSignal),
    );
  });

  it("shows unsupported, disabled-server, denied, enabled, and API-error states without prompting", async () => {
    mockServer();
    render(<NotificationSettings />);
    expect(await screen.findByText("이 브라우저에서는 푸시 알림을 지원하지 않아요.")).toBeVisible();
    expect(vi.mocked(getNotificationConfig)).not.toHaveBeenCalled();
    cleanup();

    installPushSupport();
    mockServer({ enabled: false, public_key: null });
    render(<NotificationSettings />);
    expect(await screen.findByText("서버 알림 기능이 꺼져 있어요.")).toBeVisible();
    cleanup();

    const denied = installPushSupport("denied");
    mockServer();
    render(<NotificationSettings />);
    expect(await screen.findByText("브라우저에서 알림을 차단했어요.")).toBeVisible();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(denied.requestPermission).not.toHaveBeenCalled();
    cleanup();

    installPushSupport();
    mockServer(undefined, { enabled: true });
    render(<NotificationSettings />);
    expect(await screen.findByText("알림이 켜져 있어요.")).toBeVisible();
    cleanup();

    installPushSupport();
    vi.mocked(getNotificationConfig).mockRejectedValue(new Error("offline"));
    render(<NotificationSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("알림 설정을 확인하지 못했어요.");
  });

  it("shows an actionable error when the server rejects subscription registration", async () => {
    installPushSupport();
    mockServer();
    vi.mocked(registerNotificationSubscription).mockRejectedValue(new Error("server rejected subscription"));
    render(<NotificationSettings />);

    fireEvent.click(await screen.findByRole("switch", { name: "도착 알림" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("알림 설정을 확인하지 못했어요.");
    expect(screen.getByRole("button", { name: "다시 확인" })).toBeVisible();
  });

  it("does not prompt again after permission is denied", async () => {
    const { requestPermission } = installPushSupport("denied");
    mockServer();
    render(<NotificationSettings />);

    await screen.findByText("브라우저에서 알림을 차단했어요.");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("unsubscribes the browser subscription before deleting its matching server endpoint", async () => {
    const { registration, subscription } = installPushSupport();
    registration.pushManager.getSubscription.mockResolvedValue(subscription);
    mockServer(undefined, { enabled: true });
    vi.mocked(unsubscribeNotification).mockResolvedValue({ ok: true });
    render(<NotificationSettings />);

    const toggle = await screen.findByRole("switch", { name: "도착 알림" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);

    await waitFor(() => expect(screen.getByText("알림이 꺼져 있어요.")).toBeVisible());
    expect(screen.getByRole("switch", { name: "도착 알림" })).toHaveAttribute("aria-checked", "false");
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
    expect(unsubscribeNotification).toHaveBeenCalledWith("https://push.example/current", expect.any(AbortSignal));
    expect(subscription.unsubscribe.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(unsubscribeNotification).mock.invocationCallOrder[0],
    );
  });

  it("aborts the status request on unmount and ignores late settlement", async () => {
    installPushSupport();
    let resolveConfig: ((value: { enabled: boolean; public_key: string | null }) => void) | undefined;
    const configPromise = new Promise<{ enabled: boolean; public_key: string | null }>((resolve) => {
      resolveConfig = resolve;
    });
    let signal: AbortSignal | undefined;
    vi.mocked(getNotificationConfig).mockImplementation((requestSignal: AbortSignal | undefined) => {
      signal = requestSignal;
      return configPromise;
    });

    const { unmount } = render(<NotificationSettings />);
    await waitFor(() => expect(signal).toBeDefined());
    unmount();
    expect(signal?.aborted).toBe(true);

    await act(async () => {
      resolveConfig?.({ enabled: true, public_key: publicKey });
      await Promise.resolve();
    });
    expect(getNotificationSubscriptionStatus).not.toHaveBeenCalled();
  });
});
