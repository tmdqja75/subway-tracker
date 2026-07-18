import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import RootLayout, { metadata } from "../app/layout";
import HomePage from "../app/page";
import { JourneyStepper } from "./journey-stepper";
import { Button } from "./ui/button";

afterEach(cleanup);

describe("rider visual shell primitives", () => {
  it("sets Korean document language and Korean-first metadata", () => {
    const markup = renderToStaticMarkup(
      <RootLayout>
        <main>내용</main>
      </RootLayout>,
    );

    expect(markup).toContain('<html lang="ko">');
    expect(metadata.description).toMatch(/[가-힣]/);
  });

  it("uses the visible application shell as its only main landmark", () => {
    render(<HomePage />);

    const main = screen.getByRole("main");
    expect(document.querySelectorAll("main")).toHaveLength(1);
    expect(main.tagName).toBe("MAIN");
    expect(main).toHaveClass("app-shell");
    expect(main).not.toHaveAttribute("aria-hidden", "true");
    expect(document.querySelector(".legacy-smoke-title")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "도시를 따라, 더 편안하게." })).toBeVisible();
  });

  it("renders journey progress as a labelled list without a navigation landmark", () => {
    render(<JourneyStepper activeStep="탑승" />);

    const steps = screen.getByRole("list", { name: "이동 단계" });
    expect(screen.queryByRole("navigation", { name: "이동 단계" })).not.toBeInTheDocument();
    expect(steps).toHaveClass("journey-stepper__steps");

    for (const label of ["검색", "경로", "탑승", "이동", "완료"]) {
      expect(screen.getByText(label)).toBeVisible();
    }

    expect(screen.getByText("탑승").closest("li")).toHaveAttribute("data-state", "active");
    expect(screen.getByText("탑승").closest("li")).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("검색").closest("li")).toHaveAttribute("data-state", "inactive");
  });

  it.each([
    ["primary", "button--primary"],
    ["secondary", "button--secondary"],
    ["danger", "button--danger"],
    ["ghost", "button--ghost"],
  ] as const)("renders the %s semantic button variant", (variant, className) => {
    render(<Button variant={variant}>확인</Button>);

    expect(screen.getByRole("button", { name: "확인" })).toHaveClass(className);
  });

  it("ships reduced-motion, focus-visible, and touch-safe accessibility CSS", () => {
    const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

    expect(css).toMatch(/:focus-visible\s*\{/);
    expect(css).toMatch(/outline:\s*3px solid/);
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(css).toMatch(/animation-duration:\s*0\.01ms/);
    expect(css).toMatch(/min-height:\s*44px/);
    expect(css).toMatch(/safe-area-inset-bottom/);
  });

  it("uses WCAG-AA normal-text tokens on their rendered backgrounds", () => {
    const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
    const contrastRatio = (foreground: string, background: string) => {
      const relativeLuminance = (hex: string) =>
        hex
          .match(/\w\w/g)!
          .map((channel) => Number.parseInt(channel, 16) / 255)
          .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4))
          .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
      const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort(
        (left, right) => right - left,
      );

      return (lighter + 0.05) / (darker + 0.05);
    };
    const normalTextTokens = [
      ["journey-stepper li", "526477", "f4f6fa"],
      ["route-preview__stop span:not\\(.route-preview__marker\\)", "526477", "ffffff"],
      ["journey-card__hint", "526477", "ffffff"],
      ["experience-notes p:not\\(.eyebrow\\)", "526477", "f4f6fa"],
    ];

    for (const [selector, foreground, background] of normalTextTokens) {
      expect(css).toMatch(new RegExp(`\\.${selector}\\s*\\{[^}]*color:\\s*#${foreground};`));
      expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(4.5);
    }

    expect(css).toMatch(/\.status-banner__detail\s*\{[^}]*color:\s*#526477;/);
    expect(css).toMatch(/\.status-banner--success\s*\{[^}]*background:\s*#f7fcf8;/);
    expect(contrastRatio("526477", "f7fcf8")).toBeGreaterThanOrEqual(4.5);
  });
});
