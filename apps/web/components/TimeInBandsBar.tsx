import type { CgmReportResponse } from "@/lib/types/api";
import { buildBandSegments } from "@/lib/report";

/**
 * Horizontal stacked time-in-bands bar (tbr2 → tar2), using the BG palette
 * shared with the local Streamlit report. Segment widths are the band
 * percentages; a legend underneath labels each band with its value.
 */
export function TimeInBandsBar({ report }: { report: CgmReportResponse }) {
  const segments = buildBandSegments(report);
  return (
    <div className="space-y-3">
      <div
        className="flex h-6 w-full overflow-hidden rounded-md"
        role="img"
        aria-label="Time in glucose bands"
      >
        {segments.map((s) =>
          s.pct > 0 ? (
            <div
              key={s.key}
              style={{ width: `${s.pct}%`, backgroundColor: s.color }}
              title={`${s.label}: ${s.pct.toFixed(1)}%`}
            />
          ) : null,
        )}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block size-2.5 rounded-sm"
              style={{ backgroundColor: s.color }}
              aria-hidden="true"
            />
            <span>
              {s.label} <span className="font-medium text-foreground">
                {s.pct.toFixed(1)}%
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
