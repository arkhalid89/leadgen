"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "@/lib/toast";
import type { LeadDetail, LeadList } from "@/lib/types";
import { LEAD_STATUSES, SOURCE_LABELS } from "@/lib/types";
import { ExternalLink, QualityChip, SourceBadge, Spinner, formatDate } from "./ui";

/** Everything known about one lead, and everything you can do to it. */
export default function LeadDrawer({
  leadId,
  lists,
  onClose,
  onChanged,
}: {
  leadId: number;
  lists: LeadList[];
  onClose: () => void;
  onChanged?: () => void;
}) {
  const toast = useToast();
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [note, setNote] = useState("");
  const [tag, setTag] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLead(await api.get<LeadDetail>(`/api/leads/${leadId}/detail`));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not load that lead");
      onClose();
    }
  }, [leadId, toast, onClose]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function addNote() {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await api.post(`/api/leads/${leadId}/notes`, { body: note.trim() });
      setNote("");
      await load();
      toast.success("Note saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save the note");
    } finally {
      setBusy(false);
    }
  }

  async function addTag() {
    const value = tag.trim();
    if (!value) return;
    try {
      await api.post("/api/tags", { lead_ids: [leadId], tags: [value] });
      setTag("");
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not add the tag");
    }
  }

  async function removeTag(value: string) {
    await api.del("/api/tags", { lead_ids: [leadId], tags: [value] });
    await load();
    onChanged?.();
  }

  async function setStatus(status: string) {
    await api.post("/api/leads/status", { lead_ids: [leadId], status });
    await load();
    onChanged?.();
    toast.success(`Marked as ${status}`);
  }

  async function addToList(listId: number) {
    await api.post(`/api/lists/${listId}/members`, { lead_ids: [leadId] });
    await load();
    onChanged?.();
    toast.success("Added to list");
  }

  async function suppress() {
    if (!lead?.email) return;
    await api.post("/api/suppression", { values: [lead.email], reason: "Added from lead" });
    toast.success("Added to do-not-contact");
  }

  const socials: [string, string][] = lead
    ? (
        [
          ["Facebook", lead.facebook],
          ["Instagram", lead.instagram],
          ["LinkedIn", lead.linkedin],
          ["Twitter", lead.twitter],
          ["YouTube", lead.youtube],
        ] as [string, string][]
      ).filter(([, url]) => url)
    : [];

  return (
    <div className="fixed inset-0 z-[80] flex justify-end bg-black/50" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-xl flex-col overflow-y-auto border-l bg-[var(--surface)]"
        onClick={(e) => e.stopPropagation()}
      >
        {!lead ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-[var(--muted)]">
            <Spinner /> Loading…
          </div>
        ) : (
          <>
            <header className="sticky top-0 z-10 border-b bg-[var(--surface)] px-5 py-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-semibold">{lead.business_name}</h2>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
                    <QualityChip quality={lead.quality} />
                    <span>{SOURCE_LABELS[lead.source] ?? lead.source}</span>
                    {lead.category ? <span>· {lead.category}</span> : null}
                    {lead.rating ? (
                      <span>
                        · ★ {lead.rating} {lead.reviews ? `(${lead.reviews})` : ""}
                      </span>
                    ) : null}
                  </div>
                </div>
                <button
                  onClick={onClose}
                  className="shrink-0 rounded p-1 text-[var(--muted)] hover:text-[var(--text)]"
                  aria-label="Close"
                >
                  ✕
                </button>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {LEAD_STATUSES.map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatus(s)}
                    className={`chip border px-2 py-1 capitalize transition-colors ${
                      lead.status === s
                        ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                        : "text-[var(--muted)] hover:bg-[var(--surface-2)]"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </header>

            <div className="space-y-5 px-5 py-4">
              <section>
                <h3 className="label">Contact</h3>
                <dl className="grid grid-cols-[7rem_1fr] gap-y-2 text-sm">
                  <dt className="text-[var(--muted)]">Email</dt>
                  <dd className="min-w-0">
                    {lead.emails?.length ? (
                      <div className="space-y-1">
                        {lead.emails.map((e) => (
                          <div key={e} className="flex items-center gap-1.5">
                            <a href={`mailto:${e}`} className="truncate text-[var(--accent)] hover:underline">
                              {e}
                            </a>
                            {e === lead.email ? (
                              <SourceBadge source={lead.email_source} status={lead.email_status} />
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <span className="text-[var(--muted)]">Not found</span>
                    )}
                  </dd>

                  <dt className="text-[var(--muted)]">Phone</dt>
                  <dd className="min-w-0">
                    {lead.phones?.length ? (
                      lead.phones.map((p) => (
                        <div key={p}>
                          <a href={`tel:${p}`} className="hover:underline">
                            {p}
                          </a>
                        </div>
                      ))
                    ) : (
                      <span className="text-[var(--muted)]">—</span>
                    )}
                  </dd>

                  <dt className="text-[var(--muted)]">Website</dt>
                  <dd className="min-w-0 truncate">
                    <ExternalLink href={lead.website}>
                      {lead.website.replace(/^https?:\/\//, "") || "—"}
                    </ExternalLink>
                  </dd>

                  <dt className="text-[var(--muted)]">Address</dt>
                  <dd className="min-w-0">{lead.address || "—"}</dd>

                  {lead.latitude ? (
                    <>
                      <dt className="text-[var(--muted)]">Map</dt>
                      <dd>
                        <a
                          className="text-[var(--accent)] hover:underline"
                          href={`https://www.google.com/maps/search/?api=1&query=${lead.latitude},${lead.longitude}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open in Google Maps
                        </a>
                      </dd>
                    </>
                  ) : null}
                </dl>
              </section>

              {socials.length ? (
                <section>
                  <h3 className="label">Social</h3>
                  <div className="flex flex-wrap gap-2">
                    {socials.map(([name, url]) => (
                      <a
                        key={name}
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="chip border px-2 py-1 text-[var(--accent)] hover:bg-[var(--surface-2)]"
                      >
                        {name}
                      </a>
                    ))}
                  </div>
                </section>
              ) : null}

              <section>
                <h3 className="label">Tags</h3>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {lead.tags?.length ? (
                    lead.tags.map((t) => (
                      <span
                        key={t}
                        className="chip border bg-[var(--surface-2)] px-2 py-1 text-[var(--muted)]"
                      >
                        {t}
                        <button
                          onClick={() => removeTag(t)}
                          className="ml-1 hover:text-[var(--bad)]"
                          aria-label={`Remove ${t}`}
                        >
                          ✕
                        </button>
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-[var(--muted)]">None yet.</span>
                  )}
                </div>
                <div className="flex gap-2">
                  <input
                    className="field"
                    placeholder="Add a tag…"
                    value={tag}
                    onChange={(e) => setTag(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addTag()}
                  />
                  <button className="btn-ghost shrink-0" onClick={addTag}>
                    Add
                  </button>
                </div>
              </section>

              <section>
                <h3 className="label">Lists</h3>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {lead.lists?.length ? (
                    lead.lists.map((l) => (
                      <span
                        key={l.id}
                        className="chip px-2 py-1"
                        style={{ background: `${l.colour}22`, color: l.colour }}
                      >
                        {l.name}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-[var(--muted)]">Not in any list.</span>
                  )}
                </div>
                {lists.length ? (
                  <select
                    className="field"
                    value=""
                    onChange={(e) => e.target.value && addToList(Number(e.target.value))}
                  >
                    <option value="">Add to a list…</option>
                    {lists.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                ) : null}
              </section>

              <section>
                <h3 className="label">Activity</h3>
                <div className="mb-2 flex gap-2">
                  <input
                    className="field"
                    placeholder="Add a note…"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addNote()}
                  />
                  <button className="btn-ghost shrink-0" onClick={addNote} disabled={busy}>
                    {busy ? <Spinner /> : "Save"}
                  </button>
                </div>
                <ol className="space-y-2">
                  {lead.notes?.length ? (
                    lead.notes.map((n) => (
                      <li key={n.id} className="rounded-lg border bg-[var(--surface-2)] p-2.5 text-sm">
                        <div className="flex items-start justify-between gap-2">
                          <p className="min-w-0 flex-1 whitespace-pre-wrap">{n.body}</p>
                          <button
                            onClick={async () => {
                              await api.del(`/api/notes/${n.id}`);
                              await load();
                            }}
                            className="shrink-0 text-[var(--muted)] hover:text-[var(--bad)]"
                            aria-label="Delete note"
                          >
                            ✕
                          </button>
                        </div>
                        <time className="mt-1 block text-xs text-[var(--muted)]">
                          {formatDate(n.created_at)}
                        </time>
                      </li>
                    ))
                  ) : (
                    <li className="text-xs text-[var(--muted)]">Nothing recorded yet.</li>
                  )}
                </ol>
              </section>

              {lead.email ? (
                <section className="border-t pt-4">
                  <button className="btn-ghost w-full text-[var(--warn)]" onClick={suppress}>
                    Add {lead.email} to do-not-contact
                  </button>
                </section>
              ) : null}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
