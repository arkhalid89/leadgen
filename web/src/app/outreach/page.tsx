"use client";

import { useCallback, useEffect, useState } from "react";
import { api, downloadCsv } from "@/lib/api";
import type { EmailTemplate, ServerConfig } from "@/lib/types";
import { Alert, EmptyState, Spinner, formatDate } from "@/components/ui";

const TYPES = [
  { id: "agency", label: "Agency" },
  { id: "saas", label: "SaaS" },
  { id: "freelance", label: "Freelance" },
  { id: "consulting", label: "Consulting" },
];

export default function OutreachPage() {
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [total, setTotal] = useState(0);
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<EmailTemplate | null>(null);

  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [website, setWebsite] = useState("");
  const [services, setServices] = useState("");
  const [outreachType, setOutreachType] = useState("agency");
  const [keyword, setKeyword] = useState("");
  const [limit, setLimit] = useState(10);
  const [useAi, setUseAi] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ templates: EmailTemplate[]; total: number }>(
        "/api/outreach/templates?limit=100",
      );
      setTemplates(data.templates);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load templates");
    }
  }, []);

  useEffect(() => {
    void load();
    api.get<ServerConfig>("/api/dashboard/config").then(setConfig).catch(() => {});
    api
      .get<{ keywords: string[] }>("/api/leads/filters")
      .then((data) => setKeywords(data.keywords))
      .catch(() => {});
  }, [load]);

  async function generate(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const result = await api.post<{ generated: number; mode: string }>(
        "/api/outreach/generate",
        {
          sender: { name, company, website, services },
          outreach_type: outreachType,
          keyword,
          limit,
          use_ai: useAi,
        },
      );
      setNotice(
        `Drafted ${result.generated} email${result.generated === 1 ? "" : "s"} using ${
          result.mode === "gemini" ? "Gemini" : "built-in templates"
        }.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not generate emails");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Outreach</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Draft personalised cold emails for the leads you have collected.
          </p>
        </div>
        {total > 0 ? (
          <button
            className="btn-ghost"
            onClick={() => downloadCsv("/api/outreach/templates/export", "email-templates.csv")}
          >
            Export CSV
          </button>
        ) : null}
      </div>

      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert kind="success">{notice}</Alert> : null}

      <form onSubmit={generate} className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          About you
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="name">
              Your name
            </label>
            <input
              id="name"
              className="field"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="company">
              Company
            </label>
            <input
              id="company"
              className="field"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="website">
              Website
            </label>
            <input
              id="website"
              className="field"
              placeholder="https://…"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="type">
              Outreach type
            </label>
            <select
              id="type"
              className="field"
              value={outreachType}
              onChange={(e) => setOutreachType(e.target.value)}
            >
              {TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="label" htmlFor="services">
            What you offer
          </label>
          <textarea
            id="services"
            className="field min-h-20"
            required
            placeholder="SEO and paid search for local service businesses"
            value={services}
            onChange={(e) => setServices(e.target.value)}
          />
        </div>

        <h2 className="pt-2 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Who to write to
        </h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <label className="label" htmlFor="keyword">
              Keyword
            </label>
            <select
              id="keyword"
              className="field"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            >
              <option value="">Any keyword</option>
              {keywords.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="limit">
              How many
            </label>
            <input
              id="limit"
              type="number"
              min={1}
              max={200}
              className="field"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 pb-2 text-sm">
              <input
                type="checkbox"
                className="accent-[var(--accent)]"
                checked={useAi}
                disabled={!config?.gemini_configured}
                onChange={(e) => setUseAi(e.target.checked)}
              />
              Write with Gemini
            </label>
          </div>
        </div>

        {config && !config.gemini_configured ? (
          <p className="text-xs text-[var(--muted)]">
            No Gemini key configured — the built-in templates will be used instead.
          </p>
        ) : null}

        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? <Spinner /> : null}
          Draft emails
        </button>
        <p className="text-xs text-[var(--muted)]">
          Drafts are saved here for you to copy. Nothing is sent from this tool.
        </p>
      </form>

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Drafted emails ({total})
        </h2>
        {templates.length === 0 ? (
          <EmptyState
            title="No drafts yet"
            body="Fill in the form above to draft emails for leads that have an email address."
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {templates.map((template) => (
              <button
                key={template.id}
                onClick={() => setOpen(template)}
                className="card text-left transition-colors hover:bg-[var(--surface-2)]"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{template.business_name}</div>
                    <div className="truncate text-xs text-[var(--muted)]">{template.email}</div>
                  </div>
                  <span className="shrink-0 text-xs text-[var(--muted)]">
                    {formatDate(template.created_at)}
                  </span>
                </div>
                <div className="mt-3 truncate text-sm font-medium">{template.subject}</div>
                <p className="mt-1 line-clamp-2 text-xs text-[var(--muted)]">{template.body}</p>
              </button>
            ))}
          </div>
        )}
      </div>

      {open ? (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-5"
          onClick={() => setOpen(null)}
        >
          <div
            className="card max-h-[85vh] w-full max-w-2xl overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-lg font-semibold">{open.business_name}</h3>
                <p className="truncate text-sm text-[var(--muted)]">{open.email}</p>
              </div>
              <button className="text-[var(--muted)] hover:text-[var(--text)]" onClick={() => setOpen(null)}>
                ✕
              </button>
            </div>
            <div className="label">Subject</div>
            <p className="mb-4 rounded-lg border bg-[var(--surface-2)] px-3 py-2 text-sm">
              {open.subject}
            </p>
            <div className="label">Body</div>
            <pre className="whitespace-pre-wrap rounded-lg border bg-[var(--surface-2)] px-3 py-2 font-sans text-sm leading-relaxed">
              {open.body}
            </pre>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="btn-ghost"
                onClick={() =>
                  navigator.clipboard.writeText(`Subject: ${open.subject}\n\n${open.body}`)
                }
              >
                Copy
              </button>
              <button
                className="btn-danger"
                onClick={async () => {
                  await api.del(`/api/outreach/templates/${open.id}`);
                  setOpen(null);
                  await load();
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
