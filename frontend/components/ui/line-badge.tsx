import { getLineColor, shortLineLabel } from "../../lib/line-colors";

type LineBadgeProps = {
  line: string | null;
};

/** Colored circle labeling a station/leg's line, e.g. green "2" for Line 2. Decorative — pair with an accessible label on the surrounding element. */
export function LineBadge({ line }: LineBadgeProps) {
  return (
    <span aria-hidden="true" className="line-badge" style={{ background: getLineColor(line) }}>
      {shortLineLabel(line)}
    </span>
  );
}
