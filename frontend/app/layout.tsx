import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Subway Tracker",
  description: "서울 지하철 이동을 차분하게 안내하는 라이더 여정 서비스",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
