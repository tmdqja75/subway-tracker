import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const appDirectory = resolve(process.cwd(), "app");

function pngDimensions(path: string) {
  const bytes = readFileSync(path);

  expect(bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))).toBe(true);

  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

describe("application branding", () => {
  it("ships a square favicon and iPhone home-screen icon", () => {
    const iconPath = resolve(appDirectory, "icon.png");
    const appleIconPath = resolve(appDirectory, "apple-icon.png");

    expect(existsSync(iconPath)).toBe(true);
    expect(existsSync(appleIconPath)).toBe(true);
    expect(pngDimensions(iconPath)).toEqual({ width: 1024, height: 1024 });
    expect(pngDimensions(appleIconPath)).toEqual({ width: 180, height: 180 });
  });

  it("defines installable-app metadata with both icon sizes", () => {
    const manifestPath = resolve(appDirectory, "manifest.ts");
    const layoutPath = resolve(appDirectory, "layout.tsx");

    expect(existsSync(manifestPath)).toBe(true);

    const manifestSource = readFileSync(manifestPath, "utf8");
    expect(manifestSource).toContain('export const dynamic = "force-static"');
    expect(manifestSource).toContain('display: "standalone"');
    expect(manifestSource).toContain('src: "/icon.png"');
    expect(manifestSource).toContain('sizes: "1024x1024"');
    expect(manifestSource).toContain('src: "/apple-icon.png"');
    expect(manifestSource).toContain('sizes: "180x180"');

    const layoutSource = readFileSync(layoutPath, "utf8");
    expect(layoutSource).toContain("appleWebApp");
    expect(layoutSource).toContain("capable: true");
  });
});
