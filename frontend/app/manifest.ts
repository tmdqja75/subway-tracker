import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Subway Tracker",
    short_name: "Subway Tracker",
    description: "서울 지하철 이동을 차분하게 안내하는 라이더 여정 서비스",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f6fa",
    theme_color: "#10233f",
    icons: [
      {
        src: "/icon.png",
        sizes: "1024x1024",
        type: "image/png",
      },
      {
        src: "/apple-icon.png",
        sizes: "180x180",
        type: "image/png",
      },
    ],
  };
}
