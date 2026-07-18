import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("Next build contract", () => {
  it("uses static export while proxying API requests to the local backend", async () => {
    expect(nextConfig.output).toBe("export");

    const rewrites = await nextConfig.rewrites?.();
    expect(rewrites).toEqual([
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ]);
  });
});
