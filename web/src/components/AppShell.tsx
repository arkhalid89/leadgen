"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Spinner } from "./ui";
import CommandPalette from "./CommandPalette";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard", icon: "▤", group: "Overview" },
  { href: "/analytics", label: "Analytics", icon: "◔", group: "Overview" },
  { href: "/search", label: "Find Leads", icon: "◎", group: "Acquire" },
  { href: "/jobs", label: "Searches", icon: "◷", group: "Acquire" },
  { href: "/leads", label: "Lead Database", icon: "▦", group: "Manage" },
  { href: "/lists", label: "Lists", icon: "☰", group: "Manage" },
  { href: "/outreach", label: "Outreach", icon: "✉", group: "Engage" },
  { href: "/settings", label: "Settings", icon: "⚙", group: "Account" },
];

const PUBLIC_ROUTES = ["/login", "/register"];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [running, setRunning] = useState<Job[]>([]);

  const isPublic = PUBLIC_ROUTES.includes(pathname);

  useEffect(() => {
    if (!loading && !user && !isPublic) router.replace("/login");
  }, [loading, user, isPublic, router]);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  // A search runs in the background, so the shell shows one everywhere.
  useEffect(() => {
    if (!user) return;
    let alive = true;
    const check = () =>
      api
        .get<{ jobs: Job[] }>("/api/jobs?limit=10")
        .then((data) => {
          if (alive) {
            setRunning(
              data.jobs.filter((j) => j.status === "running" || j.status === "queued"),
            );
          }
        })
        .catch(() => {});
    void check();
    const timer = setInterval(check, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [user, pathname]);

  if (isPublic) return <>{children}</>;

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center gap-3 text-[var(--muted)]">
        <Spinner />
        <span className="text-sm">Loading…</span>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-start">
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col overflow-y-auto border-r bg-[var(--surface)] transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          menuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-2 border-b px-5 py-4">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--accent)] text-sm font-bold text-white">
            L
          </span>
          <span className="text-base font-semibold tracking-tight">LeadGen</span>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
          {NAV.map((item, i) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const newGroup = i === 0 || NAV[i - 1].group !== item.group;
            return (
              <div key={item.href}>
                {newGroup ? (
                  <div className="px-3 pb-1 pt-3 text-[0.66rem] font-semibold uppercase tracking-wider text-[var(--muted)]">
                    {item.group}
                  </div>
                ) : null}
                <Link
                  href={item.href}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
                      : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"
                  }`}
                >
                  <span className="w-4 text-center">{item.icon}</span>
                  {item.label}
                </Link>
              </div>
            );
          })}
        </nav>

        {running.length ? (
          <Link
            href={`/jobs/${running[0].id}`}
            className="mx-3 mb-2 block rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2"
          >
            <div className="flex items-center gap-2 text-xs font-medium text-[var(--accent)]">
              <Spinner className="h-3 w-3" />
              {running.length} search{running.length === 1 ? "" : "es"} running
            </div>
            <div className="mt-1 truncate text-[0.68rem] text-[var(--muted)]">
              {running[0].keyword} — {running[0].progress}%
            </div>
          </Link>
        ) : null}

        <button
          onClick={() =>
            window.dispatchEvent(
              new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }),
            )
          }
          className="mx-3 mb-2 flex items-center justify-between rounded-lg border px-3 py-2 text-xs text-[var(--muted)] hover:bg-[var(--surface-2)]"
        >
          <span>Quick search…</span>
          <kbd className="rounded border px-1.5 py-0.5 text-[0.65rem]">⌘K</kbd>
        </button>

        <div className="border-t p-3">
          {!user.is_active ? (
            <Link
              href="/activate"
              className="mb-2 block rounded-lg border border-[rgba(251,191,36,0.4)] bg-[rgba(251,191,36,0.1)] px-3 py-2 text-xs text-[var(--warn)]"
            >
              Account not activated — add a licence key
            </Link>
          ) : null}
          <div className="flex items-center gap-2 rounded-lg px-2 py-2">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[var(--surface-2)] text-xs">
              {(user.full_name || user.email).slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm">{user.full_name || "Account"}</div>
              <div className="truncate text-xs text-[var(--muted)]">{user.email}</div>
            </div>
            <button
              onClick={() => void logout()}
              title="Sign out"
              className="rounded p-1 text-[var(--muted)] hover:text-[var(--bad)]"
            >
              ⏻
            </button>
          </div>
        </div>
      </aside>

      {menuOpen ? (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setMenuOpen(false)}
        />
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b px-4 py-3 lg:hidden">
          <button className="btn-ghost px-3 py-1.5" onClick={() => setMenuOpen(true)}>
            ☰
          </button>
          <span className="font-semibold">LeadGen</span>
        </header>
        <main className="min-w-0 flex-1 p-4 sm:p-5 lg:p-8">{children}</main>
      </div>
      <CommandPalette />
    </div>
  );
}
