import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

type Listener = (event: Record<string, unknown>) => void;

type WorkerScope = {
  addEventListener: (type: string, listener: Listener) => void;
  clients: {
    matchAll: ReturnType<typeof vi.fn>;
    openWindow: ReturnType<typeof vi.fn>;
  };
  location: { origin: string };
  registration: {
    showNotification: ReturnType<typeof vi.fn>;
  };
};

const workerPath = resolve(process.cwd(), "public/service-worker.js");

function loadWorker(scope: WorkerScope) {
  const workerSource = readFileSync(workerPath, "utf8");
  new Function("self", workerSource)(scope);
}

function makeScope(): { scope: WorkerScope; listeners: Map<string, Listener> } {
  const listeners = new Map<string, Listener>();
  const scope: WorkerScope = {
    addEventListener: (type, listener) => listeners.set(type, listener),
    clients: {
      matchAll: vi.fn(),
      openWindow: vi.fn(),
    },
    location: { origin: "https://tracker.example.test" },
    registration: {
      showNotification: vi.fn(),
    },
  };

  return { scope, listeners };
}

describe("root service worker", () => {
  it("shows the exact server body as a visible notification", async () => {
    const { scope, listeners } = makeScope();
    loadWorker(scope);
    const waitUntil = vi.fn();

    listeners.get("push")?.({
      data: {
        json: () => ({
          title: "Subway Tracker",
          body: "다음 역은 도곡이에요. 하차를 준비하세요.",
          url: "/",
        }),
      },
      waitUntil,
    });

    expect(scope.registration.showNotification).toHaveBeenCalledWith("Subway Tracker", {
      body: "다음 역은 도곡이에요. 하차를 준비하세요.",
      data: { url: "/" },
    });
    expect(waitUntil).toHaveBeenCalledOnce();
    await waitUntil.mock.calls[0][0];
  });

  it("focuses an existing same-origin client when a notification is clicked", async () => {
    const { scope, listeners } = makeScope();
    const focus = vi.fn();
    scope.clients.matchAll.mockResolvedValue([
      { url: "https://elsewhere.example.test/", focus: vi.fn() },
      { url: "https://tracker.example.test/current-journey", focus },
    ]);
    loadWorker(scope);
    const close = vi.fn();
    const waitUntil = vi.fn();

    listeners.get("notificationclick")?.({ notification: { close }, waitUntil });

    expect(close).toHaveBeenCalledOnce();
    expect(waitUntil).toHaveBeenCalledOnce();
    await waitUntil.mock.calls[0][0];
    expect(focus).toHaveBeenCalledOnce();
    expect(scope.clients.openWindow).not.toHaveBeenCalled();
  });

  it("opens the root route when no same-origin client exists", async () => {
    const { scope, listeners } = makeScope();
    scope.clients.matchAll.mockResolvedValue([]);
    loadWorker(scope);
    const waitUntil = vi.fn();

    listeners.get("notificationclick")?.({ notification: { close: vi.fn() }, waitUntil });

    await waitUntil.mock.calls[0][0];
    expect(scope.clients.openWindow).toHaveBeenCalledWith("/");
  });
});
