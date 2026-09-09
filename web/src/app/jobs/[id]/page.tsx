"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, api, downloadCsv } from "@/lib/api";
import type { Job, JobEvent, Lead } from "@/lib/types";
import { SOURCE_LABELS } from "@/lib/types";
import {
  Alert,
  ContactList,
  ExternalLink,
  SourceBadge,
  ProgressBar,
  QualityChip,
  Spinner,
  StatCard,
  StatusChip,
  formatDate,
} from "@/components/ui";

const LIVE_STATUSES = new Set(["queued", "running"]);

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const loadLeads = useCallback(async () => {
    try {
      const data = await api.get<{ leads: Lead[] }>(`/api/jobs/${id}/leads?limit=1000`);
      setLeads(data.leads);
    } catch {
      /* leads may not exist yet */
    }
  }, [id]);

  const loadJob = useCallback(async () => {
    try {
      setJob(await api.get<Job>(`/api/jobs/${id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this search");
    }
  }, [id]);

  useEffect(() => {
    void loadJob();
    void loadLeads();
    api
      .get<{ events: JobEvent[] }>(`/api/jobs/${id}/events?limit=300`)
      .then((data) => setEvents(data.events))
      .catch(() => {});
  }, [id, loadJob, loadLeads]);

  // Live progress over SSE while the job is active.
  useEffect(() => {
    if (!job || !LIVE_STATUSES.has(job.status)) return;

    const stream = new EventSource(`${API_BASE}/api/jobs/${id}/stream`, {
      withCredentials: true,
    });

    stream.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "progress") {
        setJob((prev) =>
          prev
            ? {
                ...prev,
                message: payload.message ?? prev.message,
                progress: payload.progress ?? prev.progress,
                stage: payload.stage ?? prev.stage,
              }
            : prev,
        );
        setEvents((prev) => [
          ...prev.slice(-299),
          {
            id: Date.now() + Math.random(),
            job_id: id,
            level: payload.level ?? "info",
            stage: payload.stage ?? "",
            progress: payload.progress ?? 0,
            message: payload.message ?? "",
            created_at: payload.at ?? new Date().toISOString(),
          },
        ]);
        // Leads are written as each phase finishes; refresh on phase markers.
        if (payload.stage === "enrich") void loadLeads();
      } else if (payload.type === "done" || payload.type === "closed") {
        void loadJob();
        void loadLeads();
        stream.close();
      }
    };

    stream.onerror = () => stream.close();
    return () => stream.close();
  }, [job?.status, id, loadJob, loadLeads]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  async function stop() {
    setStopping(true);
    try {
      await api.post(`/api/jobs/${id}/stop`);
      await loadJob();
    } finally {
      setStopping(false);
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!job)
    return (
      <div className="flex items-center gap-2 text-[var(--muted)]">
        <Spinner /> Loading…
      </div>
    );

  const live = LIVE_STATUSES.has(job.status);
  const withEmail = leads.filter((l) => l.email).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/jobs" className="text-sm text-[var(--muted)] hover:underline">
            ← All searches
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">
            {job.keyword}
            {job.location ? (
              <span className="font-normal text-[var(--muted)]"> in {job.location}</span>
            ) : null}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-[var(--muted)]">
            <StatusChip status={job.status} />
            <span>{SOURCE_LABELS[job.source] ?? job.source}</span>
            <span>·</span>
            <span>{formatDate(job.created_at)}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {live ? (
            <button className="btn-danger" onClick={stop} disabled={stopping}>
              {stopping ? <Spinner /> : null}
              Stop
            </button>
          ) : null}
          {leads.length > 0 ? (
            <button
              className="btn-ghost"
              onClick={() =>
                downloadCsv(`/api/leads/export?job_id=${job.id}`, `${job.keyword}-leads.csv`)
              }
            >
              Export CSV
            </button>
          ) : null}
        </div>
      </div>

      {live ? (
        <div className="card space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2">
              <Spinner />
              {job.message || "Working…"}
            </span>
            <span className="tabular-nums text-[var(--muted)]">{job.progress}%</span>
          </div>
          <ProgressBar value={job.progress} animated />
        </div>
      ) : null}

      {job.status === "failed" && job.error ? <Alert>{job.error}</Alert> : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Leads found" value={leads.length} />
        <StatCard label="With email" value={withEmail} tone="good" />
        <StatCard label="Enriched" value={job.enriched_count} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Leads
          </h2>
          {leads.length === 0 ? (
            <div className="card py-10 text-center text-sm text-[var(--muted)]">
              {live ? "Collecting…" : "No leads were found for this search."}
            </div>
          ) : (
            <div className="table-wrap max-h-[560px] overflow-y-auto">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Business</th>
                    <th>Email</th>
                    <th>Phone</th>
                    <th>Website</th>
                    <th>Address</th>
                    <th>Quality</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.map((lead) => (
                    <tr key={lead.id}>
                      <td className="max-w-56 truncate font-medium">{lead.business_name}</td>
                      <td>
                        <span className="inline-flex items-start">
                          <ContactList values={lead.emails} primary={lead.email} kind="email" />
                          <SourceBadge source={lead.email_source} status={lead.email_status} />
                        </span>
                      </td>
                      <td>
                        <ContactList values={lead.phones} primary={lead.phone} kind="phone" />
                      </td>
                      <td className="max-w-48 truncate">
                        <ExternalLink href={lead.website}>
                          {lead.website.replace(/^https?:\/\//, "")}
                        </ExternalLink>
                      </td>
                      <td className="max-w-64 truncate text-[var(--muted)]">
                        {lead.address || "—"}
                      </td>
                      <td>
                        <QualityChip quality={lead.quality} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="min-w-0">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
            Activity log
          </h2>
          <div
            ref={logRef}
            className="card max-h-[560px] space-y-1.5 overflow-y-auto p-4 font-mono text-xs leading-relaxed"
          >
            {events.length === 0 ? (
              <p className="text-[var(--muted)]">No activity recorded.</p>
            ) : (
              events.map((event) => (
                <div key={event.id} className="flex gap-2">
                  <span className="shrink-0 text-[var(--muted)]">
                    {event.progress ? `${String(event.progress).padStart(3)}%` : "   ·"}
                  </span>
                  <span
                    className={
                      event.level === "error" ? "text-[var(--bad)]" : "text-[var(--text)]"
                    }
                  >
                    {event.message}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
