"use client";

import { useRouter } from "next/navigation";
import type { HeatmapResponse } from "@/lib/types/api";
import {
  colorbarStops,
  colorbarTicks,
  heatmapColor,
  HEATMAP_Z_MAX,
  HEATMAP_Z_MIN,
} from "@/lib/heatmap-color";

const NO_DATA = "#f1f5f9";

/** Is this YYYY-MM-DD a Monday? Used to draw subtle weekly separators. */
function isMonday(dateIso: string): boolean {
  // Parse as local midnight to avoid TZ drift on the weekday read.
  const [y, m, d] = dateIso.split("-").map(Number);
  return new Date(y, m - 1, d).getDay() === 1;
}

export function HeatmapGrid({
  data,
  low,
  high,
}: {
  data: HeatmapResponse;
  low: number;
  high: number;
}) {
  const router = useRouter();
  const goToDay = (date: string) => router.push(`/day/${date}`);

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const dates = Array.from(new Set(data.cells.map((c) => c.date))).sort();
  const valueByKey = new Map(
    data.cells.map((c) => [`${c.date}-${c.hour}`, c.median_bg ?? c.avg_bg]),
  );
  const countByKey = new Map(data.cells.map((c) => [`${c.date}-${c.hour}`, c.n]));

  // Grid: a leading hour-label column + one column per date.
  const gridTemplateColumns = `3.25rem repeat(${dates.length}, minmax(0.75rem, 1fr))`;

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <div
          role="img"
          aria-label={`Median blood glucose heatmap, hour of day by date, ${dates.length} days, colour-coded from blue (low) through green (in range) to red (high).`}
          className="grid gap-px"
          style={{ gridTemplateColumns }}
        >
          {/* Header row: corner + date labels */}
          <div aria-hidden="true" />
          {dates.map((d) => (
            <div
              key={`h-${d}`}
              className="truncate pb-1 text-center text-[9px] text-muted-foreground"
              title={d}
            >
              {d.slice(5)}
            </div>
          ))}

          {/* One row per hour-of-day (00:00 at top) */}
          {hours.map((h) => (
            <HourRow
              key={h}
              hour={h}
              dates={dates}
              valueByKey={valueByKey}
              countByKey={countByKey}
              low={low}
              high={high}
              onSelect={goToDay}
            />
          ))}
        </div>
      </div>

      <Colorbar low={low} high={high} />
    </div>
  );
}

function HourRow({
  hour,
  dates,
  valueByKey,
  countByKey,
  low,
  high,
  onSelect,
}: {
  hour: number;
  dates: string[];
  valueByKey: Map<string, number | null>;
  countByKey: Map<string, number>;
  low: number;
  high: number;
  onSelect: (date: string) => void;
}) {
  const hourLabel = `${String(hour).padStart(2, "0")}:00`;
  return (
    <>
      <div className="pr-2 text-right text-[9px] leading-4 text-muted-foreground">
        {hour % 3 === 0 ? hourLabel : ""}
      </div>
      {dates.map((d) => {
        const key = `${d}-${hour}`;
        const v = valueByKey.get(key) ?? null;
        const n = countByKey.get(key) ?? 0;
        const monday = isMonday(d);
        const label =
          v != null
            ? `${d} ${hourLabel} — ${Math.round(v)} mg/dL (n=${n})`
            : `${d} ${hourLabel} — no readings`;
        return (
          <div
            key={key}
            role="button"
            tabIndex={0}
            aria-label={`${label}. Open day view for ${d}.`}
            title={label}
            onClick={() => onSelect(d)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(d);
              }
            }}
            className={
              "h-3.5 cursor-pointer rounded-[1px] outline-none focus-visible:ring-2 focus-visible:ring-ring" +
              (monday ? " border-l border-l-foreground/20" : "")
            }
            style={{ backgroundColor: heatmapColor(v, low, high) }}
          />
        );
      })}
    </>
  );
}

function Colorbar({ low, high }: { low: number; high: number }) {
  const stops = colorbarStops(low, high, 32);
  const ticks = colorbarTicks(low, high);
  const gradient = `linear-gradient(to right, ${stops.join(", ")})`;
  const pct = (v: number) =>
    ((v - HEATMAP_Z_MIN) / (HEATMAP_Z_MAX - HEATMAP_Z_MIN)) * 100;

  return (
    <div className="max-w-md">
      <div
        className="h-3 w-full rounded"
        style={{ background: gradient }}
        aria-hidden="true"
      />
      <div className="relative mt-1 h-4 text-[10px] text-muted-foreground">
        {ticks.map((t) => (
          <span
            key={t}
            className="absolute -translate-x-1/2 whitespace-nowrap"
            style={{ left: `${pct(t)}%` }}
          >
            {t}
          </span>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground">
        Median BG (mg/dL) · in-range {low}–{high}
      </p>
      <span className="sr-only">
        Colour scale runs from {HEATMAP_Z_MIN} mg/dL (blue) through the in-range
        band {low} to {high} mg/dL (green) up to {HEATMAP_Z_MAX} mg/dL (red).
      </span>
      <div className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
        <span
          className="inline-block h-3 w-3 rounded-[1px]"
          style={{ backgroundColor: NO_DATA }}
          aria-hidden="true"
        />
        No readings
      </div>
    </div>
  );
}
