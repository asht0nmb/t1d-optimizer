import type { CgmReportResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { TimeInBandsBar } from "@/components/TimeInBandsBar";
import { formatMetric, sufficiencyNote } from "@/lib/report";

function Tile({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children?: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-2xl font-semibold tabular-nums text-foreground">
        {value}
      </span>
      {children}
    </Card>
  );
}

/**
 * Renders the full clinical report: a sufficiency note (when withheld), the
 * headline GRI/GMI/Mean/CV + TIR/TITR/TBR/TAR tiles, the time-in-bands bar, and
 * a risk & variability detail section. All numbers come from the worker
 * (core.metrics); "—" marks an undefined metric.
 */
export function ReportTiles({ report }: { report: CgmReportResponse }) {
  const note = sufficiencyNote(report);

  return (
    <div className="space-y-5">
      {note ? (
        <div
          role="status"
          className="rounded-lg border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-foreground"
        >
          {note}
        </div>
      ) : null}

      <section
        aria-label="Headline metrics"
        className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      >
        <Tile label="GRI" value={formatMetric(report.gri, { digits: 0 })} />
        <Tile label="GMI" value={formatMetric(report.gmi, { suffix: "%" })} />
        <Tile
          label="Mean BG"
          value={formatMetric(report.mean_bg, { digits: 0, suffix: " mg/dL" })}
        />
        <Tile label="CV" value={formatMetric(report.cv_pct, { digits: 0, suffix: "%" })}>
          {report.cv_stable === null ? null : (
            <Badge variant={report.cv_stable ? "success" : "warning"}>
              {report.cv_stable ? "stable" : "high"}
            </Badge>
          )}
        </Tile>
      </section>

      <section
        aria-label="Time in range"
        className="grid grid-cols-2 gap-3 sm:grid-cols-4"
      >
        <Tile
          label="TIR (70–180)"
          value={formatMetric(report.tir, { digits: 0, suffix: "%" })}
        />
        <Tile
          label="TITR (70–140)"
          value={formatMetric(report.titr, { digits: 0, suffix: "%" })}
        />
        <Tile
          label="Below 70"
          value={formatMetric(report.tbr_total, { digits: 0, suffix: "%" })}
        />
        <Tile
          label="Above 180"
          value={formatMetric(report.tar_total, { digits: 0, suffix: "%" })}
        />
      </section>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Time in bands</h2>
        <TimeInBandsBar report={report} />
      </Card>

      <details className="rounded-lg border border-border bg-card">
        <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold">
          Risk &amp; variability detail
        </summary>
        <div className="grid grid-cols-2 gap-3 p-4 pt-0 sm:grid-cols-4">
          <Tile label="LBGI" value={formatMetric(report.lbgi, { digits: 1 })} />
          <Tile label="HBGI" value={formatMetric(report.hbgi, { digits: 1 })} />
          <Tile label="eA1c" value={formatMetric(report.ea1c, { suffix: "%" })} />
          <Tile
            label="MAGE"
            value={formatMetric(report.mage, { digits: 0, suffix: " mg/dL" })}
          />
          <Tile label="MODD" value={formatMetric(report.modd, { digits: 1 })} />
          <Tile label="CONGA" value={formatMetric(report.conga, { digits: 1 })} />
          <Tile label="J-index" value={formatMetric(report.j_index, { digits: 1 })} />
        </div>
      </details>

      <p className="text-xs text-muted-foreground">
        Computed by core/metrics (single source of truth) over the {report.days}
        -day window. Observations only — not medical advice.
      </p>
    </div>
  );
}
