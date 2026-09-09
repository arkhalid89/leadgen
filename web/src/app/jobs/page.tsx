"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";
import { SOURCE_LABELS } from "@/lib/types";
import { Alert, EmptyState, ProgressBar, Spinner, StatusChip, formatDate } from "@/components/ui";

const PAGE_SIZE = 25;

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ jobs: Job[]; total: number }>(
        `/api/jobs?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`,
      );
      setJobs(data.jobs);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load searches");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while anything is still running so the list stays current.
  useEffect(() => {
    if (!jobs.some((job) => job.status === "running" || job.status === "queued")) return;
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [jobs, load]);

  async function remove(id: string) {
    if (!confirm("Delete this search and every lead it produced?")) return;
    try {
      await api.del(`/api/jobs/${id}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the search");
    }
  }

  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Searches</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {total} {total === 1 ? "search" : "searches"} run.
          </p>
        </div>
        <Link href="/search" className="btn-primary">
          New search
        </Link>
      </div>

      {error ? <Alert>{error}</Alert> : null}

      {loading ? (
        <div className="flex items-center gap-2 text-[var(--muted)]">
          <Spinner /> Loading…
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState
          title="No searches yet"
          body="Every search you run is kept here with its full activity log and results."
          action={{ href: "/search", label: "Find leads" }}
        />
      ) : (
        <>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th>Location</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Leads</th>
                  <th>Enriched</th>
                  <th>Started</th>
                  <th />
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
                    <td className="text-[var(--muted)]">
                      {SOURCE_LABELS[job.source] ?? job.source}
                    </td>
                    <td>
                      <StatusChip status={job.status} />
                    </td>
                    <td className="w-32">
                      {job.status === "running" || job.status === "queued" ? (
                        <ProgressBar value={job.progress} animated />
                      ) : (
                        <span className="text-[var(--muted)]">—</span>
                      )}
                    </td>
                    <td className="tabular-nums">{job.total_found}</td>
                    <td className="tabular-nums text-[var(--muted)]">{job.enriched_count}</td>
                    <td className="text-[var(--muted)]">{formatDate(job.created_at)}</td>
                    <td>
                      <button
                        onClick={() => remove(job.id)}
                        className="text-[var(--muted)] hover:text-[var(--bad)]"
                        title="Delete"
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pages > 1 ? (
            <div className="flex items-center justify-between text-sm">
              <button
                className="btn-ghost"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </button>
              <span className="text-[var(--muted)]">
                Page {page + 1} of {pages}
              </span>
              <button
                className="btn-ghost"
                disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
