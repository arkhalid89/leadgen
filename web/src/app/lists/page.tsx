"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, downloadCsv } from "@/lib/api";
import { useToast } from "@/lib/toast";
import type { LeadList, SavedSearch, TagCount } from "@/lib/types";
import { EmptyState, Spinner, formatDate } from "@/components/ui";

const COLOURS = ["#4f7cff", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#38bdf8"];

export default function ListsPage() {
  const toast = useToast();
  const [lists, setLists] = useState<LeadList[]>([]);
  const [tags, setTags] = useState<TagCount[]>([]);
  const [searches, setSearches] = useState<SavedSearch[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [colour, setColour] = useState(COLOURS[0]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, t, s] = await Promise.all([
        api.get<{ lists: LeadList[] }>("/api/lists"),
        api.get<{ tags: TagCount[] }>("/api/tags"),
        api.get<{ searches: SavedSearch[] }>("/api/saved-searches"),
      ]);
      setLists(l.lists);
      setTags(t.tags);
      setSearches(s.searches);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not load your lists");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createList(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.post("/api/lists", { name: name.trim(), colour });
      setName("");
      await load();
      toast.success("List created");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create the list");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Lists &amp; segments</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Group leads into working sets, label them with tags, and keep the searches worth
          repeating.
        </p>
      </div>

      <section>
        <h2 className="label">Create a list</h2>
        <form onSubmit={createList} className="card flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <label className="label" htmlFor="listname">
              Name
            </label>
            <input
              id="listname"
              className="field"
              placeholder="e.g. Milan restaurants — round 1"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <span className="label">Colour</span>
            <div className="flex gap-1.5">
              {COLOURS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColour(c)}
                  aria-label={`Colour ${c}`}
                  className={`h-8 w-8 rounded-lg border-2 transition-transform ${
                    colour === c ? "scale-110 border-white" : "border-transparent"
                  }`}
                  style={{ background: c }}
                />
              ))}
            </div>
          </div>
          <button className="btn-primary" disabled={busy || !name.trim()}>
            {busy ? <Spinner /> : null}
            Create
          </button>
        </form>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Your lists ({lists.length})
        </h2>
        {loading ? (
          <div className="flex items-center gap-2 text-[var(--muted)]">
            <Spinner /> Loading…
          </div>
        ) : lists.length === 0 ? (
          <EmptyState
            title="No lists yet"
            body="Select leads in the database and add them to a list to start organising your outreach."
            action={{ href: "/leads", label: "Open lead database" }}
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {lists.map((list) => (
              <div key={list.id} className="card flex flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="h-3 w-3 shrink-0 rounded-full"
                      style={{ background: list.colour }}
                    />
                    <span className="truncate font-medium">{list.name}</span>
                  </div>
                  <span className="shrink-0 text-sm tabular-nums text-[var(--muted)]">
                    {list.lead_count}
                  </span>
                </div>
                {list.description ? (
                  <p className="line-clamp-2 text-xs text-[var(--muted)]">{list.description}</p>
                ) : null}
                <div className="mt-auto flex flex-wrap gap-2 text-sm">
                  <Link href={`/leads?list_id=${list.id}`} className="btn-ghost px-3 py-1.5">
                    Open
                  </Link>
                  <button
                    className="btn-ghost px-3 py-1.5"
                    onClick={() =>
                      downloadCsv(
                        `/api/leads/export?list_id=${list.id}`,
                        `${list.name.replace(/\W+/g, "-")}.csv`,
                      )
                    }
                  >
                    Export
                  </button>
                  <button
                    className="ml-auto px-2 text-[var(--muted)] hover:text-[var(--bad)]"
                    onClick={async () => {
                      if (!confirm(`Delete the list "${list.name}"? The leads themselves stay.`))
                        return;
                      await api.del(`/api/lists/${list.id}`);
                      await load();
                      toast.success("List deleted");
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Tags
        </h2>
        {tags.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            No tags yet. Tag leads from the database or a lead&apos;s detail panel.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {tags.map((t) => (
              <Link
                key={t.tag}
                href={`/leads?tag=${encodeURIComponent(t.tag)}`}
                className="chip border bg-[var(--surface)] px-2.5 py-1.5 hover:bg-[var(--surface-2)]"
              >
                {t.tag}
                <span className="ml-1.5 tabular-nums text-[var(--muted)]">{t.count}</span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Saved searches
        </h2>
        {searches.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">
            None saved. Save a search from the Find Leads page to re-run it later.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Keyword</th>
                  <th>Location</th>
                  <th>Saved</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {searches.map((s) => (
                  <tr key={s.id}>
                    <td className="font-medium">{s.name}</td>
                    <td className="text-[var(--muted)]">{s.keyword}</td>
                    <td className="text-[var(--muted)]">{s.location || "—"}</td>
                    <td className="text-[var(--muted)]">{formatDate(s.created_at)}</td>
                    <td className="flex gap-2">
                      <Link
                        className="text-[var(--accent)] hover:underline"
                        href={`/search?source=${s.source}&keyword=${encodeURIComponent(
                          s.keyword,
                        )}&location=${encodeURIComponent(s.location)}`}
                      >
                        Run
                      </Link>
                      <button
                        className="text-[var(--muted)] hover:text-[var(--bad)]"
                        onClick={async () => {
                          await api.del(`/api/saved-searches/${s.id}`);
                          await load();
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
