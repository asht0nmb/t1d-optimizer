"use client";
import { fetchJson } from "@/lib/fetch-json";

import { useCallback, useEffect, useState } from "react";
import { AgpChart } from "@/components/AgpChart";
import type { AgpResponse } from "@/lib/types/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";

export default function AgpPage() {
  const [days, setDays] = useState<14 | 30 | 90>(30);
  const [data, setData] = useState<AgpResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await fetchJson<AgpResponse>(`/api/agp?days=${days}`);
      if (body.error) setError(body.error);
      else setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load AGP");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasData = data?.hours.some((h) => h.n > 0) ?? false;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Ambulatory glucose profile</h1>
      <div className="flex gap-2">
        {([14, 30, 90] as const).map((d) => (
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
          <Card className="p-4">
            <Skeleton className="h-80 w-full" />
          </Card>
        ) : error ? (
          <ErrorState message={error} onRetry={() => void load()} />
        ) : data && !hasData ? (
          <EmptyState
            title="No AGP data"
            description="No CGM readings in the selected window."
          />
        ) : data ? (
          <Card className="p-4">
            <AgpChart data={data} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}
