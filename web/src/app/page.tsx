"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DashboardStats, Job, ServerConfig } from "@/lib/types";
import { SOURCE_LABELS } from "@/lib/types";
import { Alert, EmptyState, StatCard, StatusChip, formatDate } from "@/components/ui";

interface TimelinePoint {
  day: string;
  leads: number;
  with_email: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [points, setPoints] = useState<TimelinePoint[]>([]);
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api.get<DashboardStats>("/api/dashboard/stats"),
      api.get<{ jobs: Job[] }>("/api/jobs?limit=6"),
      api.get<{ points: TimelinePoint[] }>("/api/dashboard/timeline?days=30"),
      api.get<ServerConfig>("/api/dashboard/config"),
    ])
      .then(([s, j, t, c]) => {
        setStats(s);
        setJobs(j.jobs);
        setPoints(t.points);
        setConfig(c);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load dashboard"));
  }, []);

  const peak = Math.max(1, ...points.map((p) => p.leads));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">Your lead pipeline at a glance.</p>
        </div>
        <Link href="/search" className="btn-primary">
          New search
        </Link>
      </div>

      {error ? <Alert>{error}</Alert> : null}

      {config && !config.gemini_configured ? (
        <Alert kind="info">
          Email discovery is running and needs no API key — addresses are read from each
          business&apos;s own website. A <code>GEMINI_API_KEY</code> is optional and only used to
          draft outreach copy; without one the built-in templates are used.
        </Alert>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total leads" value={stats?.leads.total ?? "—"} />
        <StatCard
          label="With email"
          value={stats?.leads.with_email ?? "—"}
          hint={stats ? `${stats.leads.email_rate}% of all leads` : undefined}
          tone="good"
        />
        <StatCard label="With phone" value={stats?.leads.with_phone ?? "—"} />
        <StatCard
          label="Searches run"
          value={stats?.jobs.total ?? "—"}
          hint={stats?.jobs.active ? `${stats.jobs.active} running now` : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Leads collected — last 30 days
          </h2>
          {points.length === 0 ? (
            <p className="py-10 text-center text-sm text-[var(--muted)]">No leads yet.</p>
          ) : (
            <div className="flex h-40 items-end gap-1">
              {points.map((point) => (
                <div
                  key={point.day}
                  className="group relative flex-1 rounded-t bg-[var(--accent)] opacity-80 transition-opacity hover:opacity-100"
                  style={{ height: `${Math.max(4, (point.leads / peak) * 100)}%` }}
                >
                  <span className="pointer-events-none absolute -top-9 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-[var(--surface-2)] px-2 py-1 text-xs group-hover:block">
                    {point.day}: {point.leads}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Lead quality
          </h2>
          {stats ? (
            <div className="space-y-3">
              {(
                [
                  ["Strong", stats.leads.strong, "var(--good)"],
                  ["Medium", stats.leads.medium, "var(--warn)"],
                  ["Weak", stats.leads.weak, "var(--muted)"],
                ] as const
              ).map(([label, value, color]) => {
                const pct = stats.leads.total ? (value / stats.leads.total) * 100 : 0;
                return (
                  <div key={label}>
                    <div className="mb-1 flex justify-between text-sm">
                      <span>{label}</span>
                      <span className="tabular-nums text-[var(--muted)]">{value}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${pct}%`, background: color }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Recent searches
          </h2>
          <Link href="/jobs" className="text-sm text-[var(--accent)] hover:underline">
            View all
          </Link>
        </div>
        {jobs.length === 0 ? (
          <EmptyState
            title="No searches yet"
            body="Run your first search to start building a lead database."
            action={{ href: "/search", label: "Find leads" }}
          />
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>Location</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Leads</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <Link
                        href={`/jobs/${job.id}`}
                        className="font-medium text-[var(--accent)] hover:underline"
                      >
                        {job.keyword}
                      </Link>
                    </td>
                    <td className="text-[var(--muted)]">{job.location || "—"}</td>
                    <td className="text-[var(--muted)]">{SOURCE_LABELS[job.source] ?? job.source}</td>
                    <td>
                      <StatusChip status={job.status} />
                    </td>
                    <td className="tabular-nums">{job.total_found}</td>
                    <td className="text-[var(--muted)]">{formatDate(job.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
