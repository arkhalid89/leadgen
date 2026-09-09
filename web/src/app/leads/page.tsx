"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, downloadCsv, qs } from "@/lib/api";
import { useToast } from "@/lib/toast";
import type { Lead, LeadList, LeadStats, TagCount } from "@/lib/types";
import { LEAD_STATUSES, SOURCE_LABELS } from "@/lib/types";
import LeadDrawer from "@/components/LeadDrawer";
import {
  Alert,
  ContactList,
  EmptyState,
  ExternalLink,
  QualityChip,
  SourceBadge,
  Spinner,
  StatCard,
  formatDate,
} from "@/components/ui";

const PAGE_SIZE = 50;

export default function LeadsPage() {
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();

  const [leads, setLeads] = useState<Lead[]>([]);
  const [stats, setStats] = useState<LeadStats | null>(null);
  const [lists, setLists] = useState<LeadList[]>([]);
  const [allTags, setAllTags] = useState<TagCount[]>([]);
  const [filterOptions, setFilterOptions] = useState<{
    sources: string[];
    keywords: string[];
    locations: string[];
  }>({ sources: [], keywords: [], locations: [] });

  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [openLead, setOpenLead] = useState<number | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [missing, setMissing] = useState<{
    missing: number;
    ai_available: boolean;
    verify_available: boolean;
  } | null>(null);

  // Filters, seeded from the URL so links from other pages land pre-filtered.
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [source, setSource] = useState(params.get("source") ?? "");
  const [quality, setQuality] = useState(params.get("quality") ?? "");
  const [keyword, setKeyword] = useState(params.get("keyword") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [tag, setTag] = useState(params.get("tag") ?? "");
  const [listId, setListId] = useState(params.get("list_id") ?? "");
  const [emailOnly, setEmailOnly] = useState(params.get("has_email") === "true");
  const [verifiedOnly, setVerifiedOnly] = useState(params.get("verified") === "true");
  const [sort, setSort] = useState("created_at");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    const focus = params.get("focus");
    if (focus) setOpenLead(Number(focus));
  }, [params]);

  const query = useMemo(
    () =>
      qs({
        search,
        source,
        quality,
        keyword,
        status,
        tag,
        list_id: listId,
        has_email: emailOnly ? true : undefined,
        verified: verifiedOnly ? true : undefined,
        sort,
        direction,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    [search, source, quality, keyword, status, tag, listId, emailOnly, verifiedOnly, sort, direction, page],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, statData] = await Promise.all([
        api.get<{ leads: Lead[]; total: number }>(`/api/leads${query}`),
        api.get<LeadStats>("/api/leads/stats"),
      ]);
      setLeads(data.leads);
      setTotal(data.total);
      setStats(statData);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load leads");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.get<typeof filterOptions>("/api/leads/filters").then(setFilterOptions).catch(() => {});
    api.get<{ lists: LeadList[] }>("/api/lists").then((d) => setLists(d.lists)).catch(() => {});
    api.get<{ tags: TagCount[] }>("/api/tags").then((d) => setAllTags(d.tags)).catch(() => {});
    api
      .get<{ missing: number; ai_available: boolean; verify_available: boolean }>(
        "/api/leads/missing-email-count",
      )
      .then(setMissing)
      .catch(() => {});
  }, []);

  useEffect(() => {
    setPage(0);
  }, [search, source, quality, keyword, status, tag, listId, emailOnly, verifiedOnly]);

  const ids = useMemo(() => [...selected], [selected]);

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => (prev.size === leads.length ? new Set() : new Set(leads.map((l) => l.id))));
  }

  function sortBy(column: string) {
    if (sort === column) setDirection((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSort(column);
      setDirection("desc");
    }
  }

  async function bulk(action: () => Promise<unknown>, message: string) {
    try {
      await action();
      toast.success(message);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "That did not work");
    }
  }

  const activeFilters = [
    search && `search: ${search}`,
    source && `source: ${source}`,
    quality && `quality: ${quality}`,
    keyword && `keyword: ${keyword}`,
    status && `status: ${status}`,
    tag && `tag: ${tag}`,
    listId && `list: ${lists.find((l) => String(l.id) === listId)?.name ?? listId}`,
    emailOnly && "has email",
    verifiedOnly && "verified only",
  ].filter(Boolean) as string[];

  function clearFilters() {
    setSearch("");
    setSource("");
    setQuality("");
    setKeyword("");
    setStatus("");
    setTag("");
    setListId("");
    setEmailOnly(false);
    setVerifiedOnly(false);
    router.replace("/leads");
  }

  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Lead database</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {total.toLocaleString()} lead{total === 1 ? "" : "s"} match your filters.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {missing && missing.missing > 0 ? (
            <button className="btn-ghost" onClick={() => setFindOpen(true)}>
              Find missing emails ({missing.missing.toLocaleString()})
            </button>
          ) : null}
          <button className="btn-ghost" onClick={() => setCleanupOpen(true)}>
            Clean up
          </button>
          <button
            className="btn-primary"
            onClick={() => downloadCsv(`/api/leads/export${query}`, "leads.csv")}
          >
            Export CSV
          </button>
        </div>
      </div>

      {error ? <Alert>{error}</Alert> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total" value={(stats?.total ?? 0).toLocaleString()} />
        <StatCard label="With email" value={(stats?.with_email ?? 0).toLocaleString()} tone="good" />
        <StatCard label="With phone" value={(stats?.with_phone ?? 0).toLocaleString()} />
        <StatCard label="Contacts found" value={(stats?.enriched ?? 0).toLocaleString()} />
      </div>

      {/* filters */}
      <div className="card space-y-3">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <input
            className="field lg:col-span-2"
            placeholder="Search name, email, phone, website…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select className="field" value={listId} onChange={(e) => setListId(e.target.value)}>
            <option value="">All lists</option>
            {lists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({l.lead_count})
              </option>
            ))}
          </select>
          <select className="field" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All tags</option>
            {allTags.map((t) => (
              <option key={t.tag} value={t.tag}>
                {t.tag} ({t.count})
              </option>
            ))}
          </select>
          <select className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {LEAD_STATUSES.map((s) => (
              <option key={s} value={s} className="capitalize">
                {s}
              </option>
            ))}
          </select>
          <select className="field" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All sources</option>
            {filterOptions.sources.map((s) => (
              <option key={s} value={s}>
                {SOURCE_LABELS[s as keyof typeof SOURCE_LABELS] ?? s}
              </option>
            ))}
          </select>
          <select className="field" value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="">All qualities</option>
            <option value="strong">Strong</option>
            <option value="medium">Medium</option>
            <option value="weak">Weak</option>
          </select>
          <select className="field" value={keyword} onChange={(e) => setKeyword(e.target.value)}>
            <option value="">All keywords</option>
            {filterOptions.keywords.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-[var(--muted)]">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={emailOnly}
              onChange={(e) => setEmailOnly(e.target.checked)}
            />
            Has an email
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={verifiedOnly}
              onChange={(e) => setVerifiedOnly(e.target.checked)}
            />
            Verified mailbox only
          </label>
          {activeFilters.length ? (
            <button className="ml-auto text-[var(--accent)] hover:underline" onClick={clearFilters}>
              Clear {activeFilters.length} filter{activeFilters.length === 1 ? "" : "s"}
            </button>
          ) : null}
        </div>
      </div>

      {/* bulk action bar */}
      {selected.size > 0 ? (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-2.5 text-sm backdrop-blur">
          <span className="font-medium">{selected.size} selected</span>

          <select
            className="field w-auto py-1.5"
            value=""
            onChange={(e) =>
              e.target.value &&
              bulk(
                () => api.post(`/api/lists/${e.target.value}/members`, { lead_ids: ids }),
                `Added ${ids.length} to the list`,
              )
            }
          >
            <option value="">Add to list…</option>
            {lists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>

          <select
            className="field w-auto py-1.5"
            value=""
            onChange={(e) =>
              e.target.value &&
              bulk(
                () => api.post("/api/leads/status", { lead_ids: ids, status: e.target.value }),
                `Marked ${ids.length} as ${e.target.value}`,
              )
            }
          >
            <option value="">Set status…</option>
            {LEAD_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>

          <button
            className="btn-ghost px-3 py-1.5"
            onClick={() => {
              const value = prompt("Tag to apply to the selected leads");
              if (value?.trim())
                void bulk(
                  () => api.post("/api/tags", { lead_ids: ids, tags: [value.trim()] }),
                  `Tagged ${ids.length} leads`,
                );
            }}
          >
            Tag
          </button>

          <button
            className="btn-ghost px-3 py-1.5"
            onClick={() => {
              const emails = leads.filter((l) => selected.has(l.id) && l.email).map((l) => l.email);
              if (!emails.length) {
                toast.info("None of the selected leads have an email");
                return;
              }
              void bulk(
                () => api.post("/api/suppression", { values: emails, reason: "Bulk suppressed" }),
                `${emails.length} added to do-not-contact`,
              );
            }}
          >
            Suppress
          </button>

          <button
            className="btn-danger ml-auto px-3 py-1.5"
            onClick={() => {
              if (!confirm(`Delete ${selected.size} lead(s)? This cannot be undone.`)) return;
              void bulk(
                () => api.post("/api/leads/bulk-delete", { ids }),
                `Deleted ${ids.length} leads`,
              );
            }}
          >
            Delete
          </button>
        </div>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 text-[var(--muted)]">
          <Spinner /> Loading…
        </div>
      ) : leads.length === 0 ? (
        <EmptyState
          title="No leads match"
          body="Adjust the filters, or run a new search to collect more leads."
          action={{ href: "/search", label: "Find leads" }}
        />
      ) : (
        <>
          {/* table on wide screens */}
          <div className="table-wrap hidden md:block">
            <table className="tbl">
              <thead>
                <tr>
                  <th className="w-10">
                    <input
                      type="checkbox"
                      className="accent-[var(--accent)]"
                      checked={selected.size === leads.length && leads.length > 0}
                      onChange={toggleAll}
                    />
                  </th>
                  {[
                    ["business_name", "Business"],
                    ["email", "Email"],
                    ["", "Phone"],
                    ["", "Website"],
                    ["", "Tags"],
                    ["quality", "Quality"],
                    ["", "Status"],
                    ["created_at", "Added"],
                  ].map(([key, label], i) => (
                    <th
                      key={i}
                      className={key ? "cursor-pointer select-none hover:text-[var(--text)]" : ""}
                      onClick={() => key && sortBy(key)}
                    >
                      {label}
                      {sort === key ? (direction === "asc" ? " ↑" : " ↓") : ""}
                    </th>
                  ))}
                  <th />
                </tr>
              </thead>
              <tbody>
                {leads.map((lead) => (
                  <tr
                    key={lead.id}
                    className="cursor-pointer"
                    onClick={() => setOpenLead(lead.id)}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        className="accent-[var(--accent)]"
                        checked={selected.has(lead.id)}
                        onChange={() => toggle(lead.id)}
                      />
                    </td>
                    <td className="max-w-56 truncate font-medium" title={lead.business_name}>
                      {lead.business_name}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <span className="inline-flex items-start">
                        <ContactList values={lead.emails} primary={lead.email} kind="email" />
                        <SourceBadge source={lead.email_source} status={lead.email_status} />
                      </span>
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <ContactList values={lead.phones} primary={lead.phone} kind="phone" />
                    </td>
                    <td className="max-w-40 truncate" onClick={(e) => e.stopPropagation()}>
                      <ExternalLink href={lead.website}>
                        {lead.website.replace(/^https?:\/\//, "")}
                      </ExternalLink>
                    </td>
                    <td className="max-w-40">
                      <div className="flex flex-wrap gap-1">
                        {(lead.tags ?? []).slice(0, 2).map((t) => (
                          <span
                            key={t}
                            className="chip bg-[var(--surface-2)] px-1.5 py-0.5 text-[var(--muted)]"
                          >
                            {t}
                          </span>
                        ))}
                        {(lead.tags?.length ?? 0) > 2 ? (
                          <span className="text-xs text-[var(--muted)]">
                            +{(lead.tags?.length ?? 0) - 2}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <QualityChip quality={lead.quality} />
                    </td>
                    <td className="capitalize text-[var(--muted)]">{lead.status ?? "new"}</td>
                    <td className="text-[var(--muted)]">{formatDate(lead.created_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className="text-[var(--muted)] hover:text-[var(--bad)]"
                        title="Delete"
                        onClick={async () => {
                          if (!confirm("Delete this lead?")) return;
                          await api.del(`/api/leads/${lead.id}`);
                          await load();
                        }}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* cards on phones */}
          <div className="space-y-2 md:hidden">
            {leads.map((lead) => (
              <button
                key={lead.id}
                onClick={() => setOpenLead(lead.id)}
                className="card w-full text-left"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate font-medium">{lead.business_name}</span>
                  <QualityChip quality={lead.quality} />
                </div>
                <div className="mt-2 space-y-1 text-xs text-[var(--muted)]">
                  {lead.email ? <div className="truncate">✉ {lead.email}</div> : null}
                  {lead.phone ? <div>☎ {lead.phone}</div> : null}
                  {lead.address ? <div className="truncate">◎ {lead.address}</div> : null}
                </div>
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between text-sm">
            <button className="btn-ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span className="text-[var(--muted)]">
              {total === 0 ? "0" : page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of{" "}
              {total.toLocaleString()}
            </span>
            <button
              className="btn-ghost"
              disabled={page + 1 >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </>
      )}

      {openLead !== null ? (
        <LeadDrawer
          leadId={openLead}
          lists={lists}
          onClose={() => setOpenLead(null)}
          onChanged={load}
        />
      ) : null}

      {cleanupOpen ? (
        <CleanupDialog
          onClose={() => setCleanupOpen(false)}
          onRun={async (options) => {
            const result = await api.post<{ total_removed: number }>("/api/leads/cleanup", options);
            setCleanupOpen(false);
            toast.success(`Removed ${result.total_removed} lead(s)`);
            await load();
          }}
        />
      ) : null}

      {findOpen && missing ? (
        <FindEmailsDialog
          missing={missing.missing}
          aiAvailable={missing.ai_available}
          verifyAvailable={missing.verify_available}
          onClose={() => setFindOpen(false)}
          onStart={async (useAi, verify) => {
            const result = await api.post<{ job_id: string }>("/api/leads/find-emails", {
              use_ai: useAi,
              verify,
              limit: 500,
            });
            setFindOpen(false);
            router.push(`/jobs/${result.job_id}`);
          }}
        />
      ) : null}
    </div>
  );
}

function CleanupDialog({
  onClose,
  onRun,
}: {
  onClose: () => void;
  onRun: (options: {
    remove_without_email: boolean;
    remove_without_phone: boolean;
    remove_duplicates: boolean;
  }) => Promise<void>;
}) {
  const [noEmail, setNoEmail] = useState(false);
  const [noPhone, setNoPhone] = useState(false);
  const [duplicates, setDuplicates] = useState(true);
  const [busy, setBusy] = useState(false);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-5" onClick={onClose}>
      <div className="card w-full max-w-md space-y-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">Clean up leads</h2>
        <p className="text-sm text-[var(--muted)]">
          This permanently deletes rows from your database. It cannot be undone.
        </p>
        <div className="space-y-2 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={duplicates}
              onChange={(e) => setDuplicates(e.target.checked)}
            />
            Remove duplicates — keeps the richest copy of each business
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={noEmail}
              onChange={(e) => setNoEmail(e.target.checked)}
            />
            Remove leads with no email address
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-[var(--accent)]"
              checked={noPhone}
              onChange={(e) => setNoPhone(e.target.checked)}
            />
            Remove leads with no phone number
          </label>
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await onRun({
                remove_without_email: noEmail,
                remove_without_phone: noPhone,
                remove_duplicates: duplicates,
              });
              setBusy(false);
            }}
          >
            {busy ? <Spinner /> : null}
            Run cleanup
          </button>
        </div>
      </div>
    </div>
  );
}

function FindEmailsDialog({
  missing,
  aiAvailable,
  verifyAvailable,
  onClose,
  onStart,
}: {
  missing: number;
  aiAvailable: boolean;
  verifyAvailable: boolean;
  onClose: () => void;
  onStart: (useAi: boolean, verify: boolean) => Promise<void>;
}) {
  const [useAi, setUseAi] = useState(false);
  const [verify, setVerify] = useState(verifyAvailable);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-5" onClick={onClose}>
      <div className="card w-full max-w-lg space-y-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold">Find missing emails</h2>
        <p className="text-sm text-[var(--muted)]">
          {missing.toLocaleString()} lead{missing === 1 ? "" : "s"} have a website but no email yet.
          This reads each site again — trying <code>www</code> and plain-http variants, localised
          contact paths, and a search-engine lookup when the site gives nothing.
        </p>

        {error ? <Alert>{error}</Alert> : null}

        <label
          className={`flex gap-3 rounded-lg border p-3 text-sm ${
            verifyAvailable ? "cursor-pointer" : "cursor-not-allowed opacity-50"
          }`}
        >
          <input
            type="checkbox"
            className="mt-0.5 accent-[var(--accent)]"
            checked={verify}
            disabled={!verifyAvailable}
            onChange={(e) => setVerify(e.target.checked)}
          />
          <span>
            <span className="block font-medium">
              Verify mailboxes over SMTP, and test common addresses
            </span>
            <span className="mt-1 block text-xs text-[var(--muted)]">
              Confirms found addresses actually accept mail, then tries <code>contact@</code>,{" "}
              <code>hr@</code>, <code>help@</code> and similar against the domain&apos;s own mail
              server. Only accepted mailboxes are kept. No mail is sent.
            </span>
          </span>
        </label>

        <label
          className={`flex gap-3 rounded-lg border p-3 text-sm ${
            aiAvailable ? "cursor-pointer" : "cursor-not-allowed opacity-50"
          }`}
        >
          <input
            type="checkbox"
            className="mt-0.5 accent-[var(--accent)]"
            checked={useAi}
            disabled={!aiAvailable}
            onChange={(e) => setUseAi(e.target.checked)}
          />
          <span>
            <span className="block font-medium">Then ask AI for whatever is still missing</span>
            <span className="mt-1 block text-xs text-[var(--muted)]">
              Batches of 20 per request. Measured precision is <strong>30%</strong> — it mostly
              answers <code>info@their-domain</code>. Saved marked{" "}
              <span className="text-[var(--warn)]">AI?</span> and unverified.
            </span>
          </span>
        </label>

        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await onStart(useAi, verify);
              } catch (err) {
                setError(err instanceof Error ? err.message : "Could not start");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner /> : null}
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
