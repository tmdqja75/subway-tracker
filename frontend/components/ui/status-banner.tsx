import type { HTMLAttributes, ReactNode } from "react";

type StatusTone = "neutral" | "success" | "warning" | "danger";

type StatusBannerProps = HTMLAttributes<HTMLDivElement> & {
  tone?: StatusTone;
  title: string;
  children?: ReactNode;
};

export function StatusBanner({
  children,
  className = "",
  title,
  tone = "neutral",
  ...props
}: StatusBannerProps) {
  return (
    <div
      className={`status-banner status-banner--${tone} ${className}`.trim()}
      role={tone === "danger" ? "alert" : "status"}
      {...props}
    >
      <span aria-hidden="true" className="status-banner__dot" />
      <div>
        <p className="status-banner__title">{title}</p>
        {children ? <p className="status-banner__detail">{children}</p> : null}
      </div>
    </div>
  );
}
