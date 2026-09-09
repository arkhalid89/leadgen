"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DashboardStats, LeadStats } from "@/lib/types";
import { SOURCE_LABELS } from "@/lib/types";
import { Spinner, StatCard } from "@/components/ui";

interface TimelinePoint {
  day: string;
  leads: number;
  with_email: number;
}

interface KeywordRow {
  keyword: string;
  leads: number;
  with_email: number;
}

/** A horizontal bar that carries its own label and value. */
function Bar({
  label,
  value,
  total,
  colour = "var(--accent)",
  suffix,
}: {
  label: string;
  value: number;
  total: number;
  colour?: string;
  suffix?: string;
}) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate capitalize">{label}</span>
        <span className="shrink-0 tabular-nums text-[var(--muted)]">
          {value.toLocaleString()}
          {suffix ? ` ${suffix}` : ""}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--surface-2)]">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(pct, value > 0 ? 1.5 : 0)}%`, background: colour }}
        />
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [leadStats, setLeadStats] = useState<LeadStats | null>(null);
  const [points, setPoints] = useState<TimelinePoint[]>([]);
  const [keywords, setKeywords] = useState<KeywordRow[]>([]);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.get<DashboardStats>("/api/dashboard/stats"),
      api.get<LeadStats>("/api/leads/stats"),
      api.get<{ points: TimelinePoint[] }>(`/api/dashboard/timeline?days=${days}`),
      api.get<{ keywords: KeywordRow[] }>("/api/dashboard/top-keywords?limit=10"),
    ])
      .then(([a, b, c, d]) => {
        setStats(a);
        setLeadStats(b);
        setPoints(c.points);
        setKeywords(d.keywords);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [days]);

  const peak = Math.max(1, ...points.map((p) => p.leads));
  const totalLeads = leadStats?.total ?? 0;
  const funnel = stats
    ? [
        { label: "Collected", value: stats.leads.total, colour: "var(--accent)" },
        { label: "Has a website", value: leadStats?.with_website ?? 0, colour: "#38bdf8" },
        { label: "Has a phone", value: stats.leads.with_phone, colour: "#a78bfa" },
        { label: "Has an email", value: stats.leads.with_email, colour: "var(--good)" },
        { label: "Contact verified", value: leadStats?.enriched ?? 0, colour: "#34d399" },
      ]
    : [];

  if (loading && !stats) {
    return (
      <div className="flex items-center gap-2 text-[var(--muted)]">
        <Spinner /> Loading analytics…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Analytics</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Where your leads come from, and how many are actually reachable.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border p-1">
          {[7, 30, 90, 365].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded px-3 py-1.5 text-sm transition-colors ${
                days === d
                  ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "text-[var(--muted)] hover:text-[var(--text)]"
              }`}
            >
              {d === 365 ? "1y" : `${d}d`}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total leads" value={totalLeads.toLocaleString()} />
        <StatCard
          label="Reachable by email"
          value={(stats?.leads.with_email ?? 0).toLocaleString()}
          hint={stats ? `${stats.leads.email_rate}% of all leads` : undefined}
          tone="good"
        />
        <StatCard
          label="Reachable by phone"
          value={(stats?.leads.with_phone ?? 0).toLocaleString()}
          hint={
            totalLeads
              ? `${Math.round(((stats?.leads.with_phone ?? 0) / totalLeads) * 100)}% of all leads`
              : undefined
          }
        />
        <StatCard
          label="Searches run"
          value={stats?.jobs.total ?? 0}
          hint={stats?.jobs.active ? `${stats.jobs.active} running` : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="card lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Leads collected — last {days} days
          </h2>
          {points.length === 0 ? (
            <p className="py-12 text-center text-sm text-[var(--muted)]">
              Nothing collected in this window.
            </p>
          ) : (
            <>
              <div className="flex h-48 items-end gap-[3px]">
                {points.map((point) => (
                  <div
                    key={point.day}
                    className="group relative flex flex-1 flex-col justify-end"
                    style={{ height: "100%" }}
                  >
                    <div
                      className="rounded-t bg-[var(--good)]"
                      style={{ height: `${(point.with_email / peak) * 100}%` }}
                    />
                    <div
                      className="rounded-b bg-[var(--accent)] opacity-70 transition-opacity group-hover:opacity-100"
                      style={{
                        height: `${((point.leads - point.with_email) / peak) * 100}%`,
                      }}
                    />
                    <span className="pointer-events-none absolute -top-10 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded border bg-[var(--surface-2)] px-2 py-1 text-xs group-hover:block">
                      {point.day}: {point.leads} ({point.with_email} w/ email)
                    </span>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex gap-4 text-xs text-[var(--muted)]">
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm bg-[var(--good)]" /> with email
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm bg-[var(--accent)] opacity-70" /> no email
                </span>
              </div>
            </>
          )}
        </div>

        <div className="card">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Reachability funnel
          </h2>
          <div className="space-y-3.5">
            {funnel.map((step) => (
              <Bar
                key={step.label}
                label={step.label}
                value={step.value}
                total={stats?.leads.total ?? 1}
                colour={step.colour}
              />
            ))}
          </div>
          <p className="mt-4 text-xs leading-relaxed text-[var(--muted)]">
            Each step is a subset of the one above. The drop from website to email is where
            enrichment earns its keep.
          </p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Best performing keywords
          </h2>
          {keywords.length === 0 ? (
            <p className="py-8 text-center text-sm text-[var(--muted)]">No keyword data yet.</p>
          ) : (
            <div className="space-y-3.5">
              {keywords.map((k) => (
                <Bar
                  key={k.keyword}
                  label={k.keyword}
                  value={k.leads}
                  total={Math.max(...keywords.map((x) => x.leads))}
                  suffix={`· ${k.with_email} w/ email`}
                />
              ))}
            </div>
          )}
        </div>

        <div className="space-y-6">
          <div className="card">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
              Lead quality
            </h2>
            <div className="space-y-3.5">
              <Bar
                label="Strong"
                value={stats?.leads.strong ?? 0}
                total={stats?.leads.total ?? 1}
                colour="var(--good)"
              />
              <Bar
                label="Medium"
                value={stats?.leads.medium ?? 0}
                total={stats?.leads.total ?? 1}
                colour="var(--warn)"
              />
              <Bar
                label="Weak"
                value={stats?.leads.weak ?? 0}
                total={stats?.leads.total ?? 1}
                colour="var(--muted)"
              />
            </div>
          </div>

          <div className="card">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
              Where leads came from
            </h2>
            <div className="space-y-3.5">
              {Object.entries(leadStats?.by_source ?? {}).map(([source, count]) => (
                <Bar
                  key={source}
                  label={SOURCE_LABELS[source as keyof typeof SOURCE_LABELS] ?? source}
                  value={count}
                  total={totalLeads || 1}
                  colour="#38bdf8"
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
