"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { DayChart } from "@/components/DayChart";
import type { DayViewResponse } from "@/lib/types/api";

export default function DayPage() {
  const params = useParams();
  const date = params.date as string;
  const [data, setData] = useState<DayViewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await fetch(`/api/day/${date}`);
      const body = await res.json();
      if (cancelled) return;
      if (!res.ok) setError(body.error ?? "Failed to load");
      else setData(body);
    })();
    return () => {
      cancelled = true;
    };
  }, [date]);

  if (error) {
    return <p className="text-red-600">{error}</p>;
  }
  if (!data) {
    return <p className="text-slate-500">Loading…</p>;
  }
  if (data.cgm.length === 0) {
    return (
      <p className="text-slate-600">
        No CGM data for {date}. Data may be stale until the nightly sync runs.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{date}</h1>
      <DayChart data={data} />
    </div>
  );
}
