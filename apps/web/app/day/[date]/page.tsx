"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DayChart } from "@/components/DayChart";
import type { DayViewResponse } from "@/lib/types/api";
import { fetchJson } from "@/lib/fetch-json";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function DayPage() {
  const params = useParams();
  const date = params.date as string;
  const [data, setData] = useState<DayViewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchJson<DayViewResponse>(`/api/day/${date}`);
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{date}</h1>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <Card className="space-y-4 p-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-32 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && data.cgm.length === 0 ? (
          <EmptyState
            title={`No CGM data for ${date}`}
            description="Data may be stale until the nightly sync runs."
          />
        ) : data ? (
          <Card className="p-4">
            <DayChart data={data} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}
