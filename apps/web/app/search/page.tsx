"use client";

import Link from "next/link";
import { useState } from "react";
import type { SearchResponse } from "@/lib/types/api";

export default function SearchPage() {
  const [tirBelow, setTirBelow] = useState("50");
  const [alarmsAbove, setAlarmsAbove] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(page = 1) {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ page: String(page), page_size: "30" });
    if (tirBelow) params.set("tir_below", tirBelow);
    if (alarmsAbove) params.set("alarms_above", alarmsAbove);
    const res = await fetch(`/api/search?${params}`);
    const body = await res.json();
    setLoading(false);
    if (!res.ok) setError(body.error ?? "Search failed");
    else setData(body);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Search days</h1>
      <form
        className="flex flex-wrap items-end gap-4 rounded-lg border border-slate-200 bg-white p-4"
        onSubmit={(e) => {
          e.preventDefault();
          run(1);
        }}
      >
        <label className="text-sm">
          TIR below (%)
          <input
            type="number"
            value={tirBelow}
            onChange={(e) => setTirBelow(e.target.value)}
            className="mt-1 block w-24 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="text-sm">
          Alarms above
          <input
            type="number"
            value={alarmsAbove}
            onChange={(e) => setAlarmsAbove(e.target.value)}
            className="mt-1 block w-24 rounded border border-slate-300 px-2 py-1"
            placeholder="optional"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          Search
        </button>
      </form>
      {error && <p className="text-red-600">{error}</p>}
      {data && (
        <>
          <p className="text-sm text-slate-600">
            {data.total} matching days (page {data.page})
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-slate-500">
                <th className="py-2">Date</th>
                <th>TIR %</th>
                <th>Alarms</th>
                <th>Lows</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={r.date} className="border-b border-slate-100">
                  <td className="py-2">
                    <Link href={`/day/${r.date}`} className="text-blue-700 hover:underline">
                      {r.date}
                    </Link>
                  </td>
                  <td>{r.tir_pct.toFixed(0)}</td>
                  <td>{r.alarm_count}</td>
                  <td>{r.low_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
