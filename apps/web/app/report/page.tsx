"use client";

import { useCallback, useEffect, useState } from "react";
import type { CgmReportResponse } from "@/lib/types/api";
import { ReportTiles } from "@/components/ReportTiles";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";

const WINDOWS = [14, 30, 90] as const;
type Window = (typeof WINDOWS)[number];

export default function ReportPage() {
  const [days, setDays] = useState<Window>(14);
  const [data, setData] = useState<CgmReportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetch(`/api/report?days=${days}`).then((r) => r.json());
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Clinical report</h1>
      <div className="flex gap-2">
        {WINDOWS.map((d) => (
          <Button
            key={d}
            type="button"
            size="sm"
            variant={days === d ? "default" : "outline"}
            onClick={() => setDays(d)}
          >
            {d}d
          </Button>
        ))}
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="space-y-3 p-4">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data ? (
          <ReportTiles report={data} />
        ) : null}
      </div>
    </div>
  );
}
