"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ThemeToggle";

interface NavLink {
  href: string;
  label: string;
}

interface NavGroup {
  heading: string;
  links: NavLink[];
}

const groups: NavGroup[] = [
  {
    heading: "Daily",
    links: [
      { href: "/day", label: "Day" },
      { href: "/compare", label: "Compare" },
    ],
  },
  {
    heading: "Trends",
    links: [
      { href: "/heatmap", label: "Heatmap" },
      { href: "/agp", label: "AGP" },
      { href: "/trends", label: "TIR" },
      { href: "/report", label: "Report" },
      { href: "/insulin", label: "Insulin" },
    ],
  },
  {
    heading: "System",
    links: [
      { href: "/search", label: "Search" },
      { href: "/alerts", label: "Alerts" },
      { href: "/status", label: "Status" },
    ],
  },
];

function isActive(href: string, pathname: string): boolean {
  if (href === "/day") return pathname.startsWith("/day");
  return pathname.startsWith(href);
}

export function AppNav() {
  const pathname = usePathname();
  if (pathname === "/login") return null;

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    window.location.href = "/login";
  }

  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Link
            href="/"
            className={cn(
              "rounded text-sm font-semibold text-foreground",
              "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card",
              pathname === "/" && "text-primary",
            )}
          >
            T1D Engine
          </Link>
          {groups.map((group) => (
            <nav
              key={group.heading}
              aria-label={group.heading}
              className="flex items-center gap-1"
            >
              <span className="mr-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.heading}
              </span>
              {group.links.map(({ href, label }) => {
                const active = isActive(href, pathname);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "rounded-md px-2 py-1 text-sm transition-colors",
                      "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card",
                      active
                        ? "bg-accent font-medium text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    {label}
                  </Link>
                );
              })}
            </nav>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <Button type="button" variant="ghost" size="sm" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
