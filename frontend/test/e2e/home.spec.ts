import { expect, test } from "@playwright/test";

test("renders the visible Subway Tracker application shell", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("Subway Tracker");
  const main = page.locator("main");
  await expect(main).toHaveCount(1);
  await expect(main).toHaveClass("app-shell");
  await expect(main).toBeVisible();
  await expect(main.getByRole("heading", { level: 1, name: "도시를 따라, 더 편안하게." })).toBeVisible();
});
