const NOTIFICATION_TITLE = "Subway Tracker";
const FALLBACK_BODY = "다음 역을 확인하세요.";
const ROOT_URL = "/";

function notificationBody(data) {
  if (!data) {
    return FALLBACK_BODY;
  }

  try {
    const payload = data.json();
    return typeof payload?.body === "string" && payload.body.trim()
      ? payload.body
      : FALLBACK_BODY;
  } catch {
    return FALLBACK_BODY;
  }
}

self.addEventListener("push", (event) => {
  const body = notificationBody(event.data);
  event.waitUntil(
    self.registration.showNotification(NOTIFICATION_TITLE, {
      body,
      data: { url: ROOT_URL },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const existingClient = clients.find((client) => {
        try {
          return new URL(client.url).origin === self.location.origin;
        } catch {
          return false;
        }
      });

      if (existingClient) {
        await existingClient.focus();
        return;
      }

      await self.clients.openWindow(ROOT_URL);
    })(),
  );
});
