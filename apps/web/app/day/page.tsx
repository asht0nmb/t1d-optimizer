"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { format } from "date-fns";
import type { ConfigResponse } from "@/lib/types/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function DayPickerPage() {
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
      <h1 className="text-2xl font-semibold">Day view</h1>
      <p className="text-sm text-muted-foreground">
        Pick a calendar day to open the multi-panel chart (CGM, bolus, basal).
      </p>
      <Card>
        <CardHeader>
          <CardTitle>Open a day</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={go} className="flex flex-wrap items-end gap-3">
            <label className="text-sm font-medium text-foreground">
              Date
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                max={maxDate}
                className="mt-1 block rounded-md border border-input bg-card px-3 py-2 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <Button type="submit">Open day</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
