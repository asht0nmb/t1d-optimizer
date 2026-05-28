"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { format } from "date-fns";
import type { ConfigResponse } from "@/lib/types/api";

export default function HomePage() {
  const router = useRouter();
  const [date, setDate] = useState(format(new Date(), "yyyy-MM-dd"));
  const [maxDate, setMaxDate] = useState<string | undefined>(undefined);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json() as Promise<ConfigResponse>)
      .then((config) => {
        const nextDate = config.date_bounds?.max_date;
        if (nextDate) {
          setDate(nextDate);
          setMaxDate(nextDate);
        }
      })
      .catch(() => {
        // Keep local-date fallback when config is unavailable.
      });
  }, []);

  function go(e: React.FormEvent) {
    e.preventDefault();
    router.push(`/day/${date}`);
  }

  return (
    <div className="max-w-md space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">Day view</h1>
      <p className="text-sm text-slate-600">
        Pick a calendar day to open the multi-panel chart (CGM, bolus, basal).
      </p>
      <form onSubmit={go} className="flex flex-wrap items-end gap-3">
        <label className="text-sm font-medium text-slate-700">
          Date
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            max={maxDate}
            className="mt-1 block rounded border border-slate-300 px-3 py-2"
          />
        </label>
        <button
          type="submit"
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Open day
        </button>
      </form>
    </div>
  );
}
