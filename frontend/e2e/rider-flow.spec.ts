import { expect, test, type Page } from "@playwright/test";

import {
  activeOnTrainJourney,
  completedJourney,
  createMockApiState,
  installMockBackend,
  pushingJourney,
  pushFailedJourney,
  timerAwaitingBoardJourney,
  type MockApiState,
} from "./fixtures";

type BrowserProblems = {
  consoleErrors: string[];
  pageErrors: string[];
};

let browserProblems: BrowserProblems | null = null;

function watchBrowserProblems(page: Page): BrowserProblems {
  const problems: BrowserProblems = { consoleErrors: [], pageErrors: [] };
  page.on("console", (message) => {
    if (message.type() === "error") {
      problems.consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => problems.pageErrors.push(error.message));
  return problems;
}

async function expectNativeTouchTarget(locator: ReturnType<Page["getByRole"]>) {
  await expect(locator).toHaveJSProperty("tagName", "BUTTON");
  await expect(locator).toBeEnabled();
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(44);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
}

async function expectOpenStreetMapTilesAreLocallyFulfilled(state: MockApiState, previousCount = 0) {
  await expect.poll(() => state.fulfilledOpenStreetMapTileRequests.length).toBeGreaterThan(previousCount);
  expect(state.fulfilledOpenStreetMapTileRequests).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        hostname: expect.stringMatching(/^(?:[a-z]+\.)?tile\.openstreetmap\.org$/),
        method: "GET",
      }),
    ]),
  );
}

async function openWithState(page: Page, state: MockApiState): Promise<BrowserProblems> {
  const problems = watchBrowserProblems(page);
  browserProblems = problems;
  await installMockBackend(page, state);
  await page.goto("/");
  await expect(page.getByRole("main", { name: "Subway Tracker 이동 화면" })).toBeVisible();
  return problems;
}

test.afterEach(async ({ page }) => {
  if (browserProblems) {
    expect(browserProblems.pageErrors, "unexpected page errors").toEqual([]);
    expect(browserProblems.consoleErrors, "unexpected console errors").toEqual([]);
  }
  browserProblems = null;
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("saved route history is side by side and fills exact station choices", async ({ page }) => {
  const state = createMockApiState({ current: { state: "idle" } });
  await openWithState(page, state);

  const mostUsed = page.getByRole("region", { name: "Most Used Route" });
  const recent = page.getByRole("region", { name: "Recent Route" });
  await expect(mostUsed).toBeVisible();
  await expect(recent).toBeVisible();
  await expect.poll(() => state.routeHistoryRequests).toBe(1);
  const savedRoute = mostUsed.getByRole("button", { name: "강남 (2호선) → 홍대입구 (2호선)" });
  await expectNativeTouchTarget(savedRoute);
  await savedRoute.click();

  await expect(page.getByRole("combobox", { name: "출발역" })).toHaveValue("강남");
  await expect(page.getByRole("combobox", { name: "도착역" })).toHaveValue("홍대입구");
  await expect(page.getByText("선택됨: 강남 · 2호선")).toHaveAttribute("role", "status");
  await expect(page.getByText("선택됨: 홍대입구 · 2호선")).toHaveAttribute("role", "status");
  await expectNoHorizontalOverflow(page);
});

test("rider selects exact stations, starts a returned route, then boards only a direction-safe train", async ({ page }) => {
  const state = createMockApiState({ current: { state: "idle" } });
  await openWithState(page, state);

  const origin = page.getByRole("combobox", { name: "출발역" });
  await origin.fill("강남");
  await expect(page.getByRole("option", { name: "강남 2호선" })).toBeVisible();
  await page.getByRole("option", { name: "강남 2호선" }).click();
  await expect(page.getByText("선택됨: 강남 · 2호선")).toHaveAttribute("role", "status");

  const destination = page.getByRole("combobox", { name: "도착역" });
  await destination.fill("홍대");
  await expect(page.getByRole("option", { name: "홍대입구 2호선" })).toBeVisible();
  await page.getByRole("option", { name: "홍대입구 2호선" }).click();
  await expect(page.getByText("선택됨: 홍대입구 · 2호선")).toHaveAttribute("role", "status");
  await expect.poll(() => state.stationSearchRequests).toEqual([
    { method: "GET", q: "강남" },
    { method: "GET", q: "홍대" },
  ]);

  await expectNativeTouchTarget(page.getByRole("button", { name: "경로 찾기" }));
  await page.getByRole("button", { name: "경로 찾기" }).click();
  await expect(page.getByRole("list", { name: "추천 경로" })).toBeVisible();
  await expect.poll(() => state.routeRequests.length).toBe(1);
  expect(state.routeRequests).toEqual([
    { start: "강남", end: "홍대입구", start_id: "0222", end_id: "0214" },
  ]);

  const routeCards = page.locator(".route-list__item > button.route-card");
  await expect(routeCards).toHaveCount(state.itineraries.length);
  for (let index = 0; index < state.itineraries.length; index += 1) {
    await expectNativeTouchTarget(routeCards.nth(index));
  }
  await routeCards.first().click();
  await expect.poll(() => state.startRequests.length).toBe(1);
  expect(state.startRequests[0]).toEqual({ itinerary: state.itineraries[0] });

  await expect(page.getByText("교대")).toBeVisible();
  const train = page.getByRole("button", { name: /2207 열차/ });
  await expectNativeTouchTarget(train);
  await expect(train).toHaveAccessibleName(/접근 중/);
  await expect(train).toContainText("성수행");
  await expect(page.getByRole("button", { name: /2208 열차/ })).toHaveCount(0);
  await expectNativeTouchTarget(page.getByRole("button", { name: "여정 취소" }));
  await train.click();
  await expect.poll(() => state.boardRequests.length).toBe(1);
  expect(state.boardRequests).toEqual([{ train_no: "2207", retroactive: false }]);
  await expect(page.getByRole("heading", { name: "수도권2호선" })).toBeVisible();
  await expect(page.getByText("2207 열차 · 역삼 · 운행 중")).toBeVisible();
  await expectOpenStreetMapTilesAreLocallyFulfilled(state);
  await expectNoHorizontalOverflow(page);
});

test("a timer-only leg remains boardable and submits the canonical null train payload", async ({ page }) => {
  const state = createMockApiState({ current: timerAwaitingBoardJourney() });
  await openWithState(page, state);

  await expect(page.getByText("이 구간은 실시간 열차 위치를 지원하지 않아요.")).toBeVisible();
  const timerBoard = page.getByRole("button", { name: "시간 기준으로 탑승 시작" });
  await expectNativeTouchTarget(timerBoard);
  await expectNativeTouchTarget(page.getByRole("button", { name: "여정 취소" }));
  await timerBoard.click();
  await expect.poll(() => state.boardRequests.length).toBe(1);
  expect(state.arrivalsRequests).toBe(0);
  expect(state.boardRequests).toEqual([{ train_no: null, retroactive: false }]);
  await expect(page.getByText("예정 이동 시간을 기준으로 여정을 기록하고 있어요.")).toBeVisible();
  await expect(page.getByRole("button", { name: "내렸어요" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("an authoritative on-train snapshot exposes accessible rider status and controls", async ({ page }) => {
  const state = createMockApiState({ current: activeOnTrainJourney() });
  await openWithState(page, state);

  await expect(page.getByRole("heading", { name: "수도권2호선" })).toBeVisible();
  await expect(page.getByText("2207 열차 · 역삼 · 운행 중")).toBeVisible();
  await expect(page.getByText("실시간 위치를 받아 여정을 기록하고 있어요.")).toBeVisible();
  await expect(page.getByText("기록된 위치 12개")).toBeVisible();
  await expectOpenStreetMapTilesAreLocallyFulfilled(state);
  await expectNativeTouchTarget(page.getByRole("button", { name: "내렸어요" }));
  await expectNativeTouchTarget(page.getByRole("button", { name: "다른 열차를 탔어요" }));
  await expectNativeTouchTarget(page.getByRole("button", { name: "여정 취소" }));
  await expectNoHorizontalOverflow(page);
});

test("a failed transfer can defer delivery and return to station selection without retrying", async ({ page }) => {
  const state = createMockApiState({ current: pushFailedJourney() });
  await openWithState(page, state);

  const deferDelivery = page.getByRole("button", { name: "데이터 나중에 다시 보내기" });
  await expectNativeTouchTarget(deferDelivery);
  await deferDelivery.click();

  await expect(page.getByRole("combobox", { name: "출발역" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "도착역" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "어디로 이동하세요?" })).toBeVisible();
  expect(state.retryRequests).toBe(0);
  await expectNoHorizontalOverflow(page);
});

test("reload renders pushing, completed, and failed transfer snapshots; retry is single-shot and refreshes state", async ({ page }) => {
  const state = createMockApiState({ current: pushingJourney() });
  await openWithState(page, state);

  const progress = page.getByRole("progressbar", { name: "이동 기록 전송 진행률" });
  await expect(progress).toHaveAttribute("aria-valuenow", "40");
  await expect(page.getByText("전송한 위치 2 / 5개 · 남은 위치 3개")).toBeVisible();

  const tileRequestsBeforeCompletion = state.fulfilledOpenStreetMapTileRequests.length;
  state.current = completedJourney();
  await page.reload();
  await expect(progress).toHaveAttribute("aria-valuenow", "100");
  await expect(page.getByRole("heading", { name: "이동 기록 전송이 완료됐어요" })).toBeVisible();
  await expectOpenStreetMapTilesAreLocallyFulfilled(state, tileRequestsBeforeCompletion);

  state.current = pushFailedJourney();
  await page.reload();
  await expect(page.locator(".transfer-status__failure[role=alert]")).toContainText("전송 중 네트워크 오류가 발생했어요.");
  await expect(page.getByText("기술 정보: upstream timed out")).toBeVisible();
  const retry = page.getByRole("button", { name: "기록을 다시 전송" });
  await expectNativeTouchTarget(retry);
  await retry.evaluate((element) => {
    const button = element as HTMLButtonElement;
    button.click();
    button.click();
  });
  await expect.poll(() => state.retryRequests).toBe(1);
  await expect(page.getByRole("heading", { name: "이동 기록 전송이 완료됐어요" })).toBeVisible();
  expect(state.retryMethods).toEqual(["POST"]);
  expect(state.retryPayloads).toEqual([null]);
  await expectNoHorizontalOverflow(page);
});
