"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

const links = [
  { href: "/", label: "Day" },
  { href: "/heatmap", label: "Heatmap" },
  { href: "/trends", label: "TIR" },
  { href: "/insulin", label: "Insulin" },
  { href: "/search", label: "Search" },
  { href: "/compare", label: "Compare" },
];

export function AppNav() {
  const pathname = usePathname();
  if (pathname === "/login") return null;

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    window.location.href = "/login";
  }

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex flex-wrap gap-1">
          <span className="mr-3 text-sm font-semibold text-slate-800">
            T1D Engine
          </span>
          {links.map(({ href, label }) => {
            const active =
              href === "/"
                ? pathname === "/" || pathname.startsWith("/day")
                : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`rounded px-2 py-1 text-sm ${
                  active
                    ? "bg-slate-100 font-medium text-slate-900"
                    : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </div>
        <button
          type="button"
          onClick={signOut}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
