"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/lib/toast";
import type { EnrichMode, Job, ServerConfig, Source } from "@/lib/types";
import { Alert, Spinner } from "@/components/ui";

const SOURCES: { id: Source; title: string; blurb: string; needsLocation: boolean }[] = [
  {
    id: "gmaps",
    title: "Google Maps",
    blurb:
      "Local businesses with addresses, phone numbers, ratings and coordinates. Best when you have a city or region in mind.",
    needsLocation: true,
  },
  {
    id: "websearch",
    title: "Web Search",
    blurb:
      "Every business website ranking for a keyword. One keyword fans out into several search variants, deduplicated by domain. Location optional.",
    needsLocation: false,
  },
];

const ENRICH: { id: EnrichMode; title: string; blurb: string }[] = [
  {
    id: "standard",
    title: "Find email addresses",
    blurb:
      "After collecting the businesses, read each one's website for published addresses and social links. Free, and every address comes off the site itself.",
  },
  {
    id: "off",
    title: "Skip email lookup",
    blurb: "Collect names, phones, addresses, websites and ratings only. Fastest.",
  },
];

const PRESETS = [10, 50, 100, 250, 500, 1000];

function SearchForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { user } = useAuth();
  const toast = useToast();

  const [source, setSource] = useState<Source>((params.get("source") as Source) || "gmaps");
  const [keyword, setKeyword] = useState(params.get("keyword") ?? "");
  const [location, setLocation] = useState(params.get("location") ?? "");
  const [maxLeads, setMaxLeads] = useState(100);
  const [unlimited, setUnlimited] = useState(false);
  const [enrichMode, setEnrichMode] = useState<EnrichMode>("standard");
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get<ServerConfig>("/api/dashboard/config").then(setConfig).catch(() => {});
  }, []);

  const activeSource = SOURCES.find((s) => s.id === source)!;
  const needsLocationForUnlimited = unlimited && !location.trim();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (needsLocationForUnlimited) {
      setError("An unlimited search needs a location to work through.");
      return;
    }
    setBusy(true);
    try {
      const job = await api.post<Job>("/api/jobs", {
        source,
        keyword: keyword.trim(),
        location: location.trim(),
        max_leads: unlimited ? 0 : maxLeads,
        enrich_mode: enrichMode,
      });
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the search");
      setBusy(false);
    }
  }

  async function saveSearch() {
    if (!keyword.trim()) return;
    setSaving(true);
    try {
      await api.post("/api/saved-searches", {
        name: `${keyword.trim()}${location.trim() ? ` — ${location.trim()}` : ""}`,
        source,
        keyword: keyword.trim(),
        location: location.trim(),
        params: { max_leads: unlimited ? 0 : maxLeads, enrich_mode: enrichMode },
      });
      toast.success("Search saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Find leads</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Pick a source, describe who you are looking for, and watch results arrive live.
        </p>
      </div>

      {!user?.is_active ? (
        <Alert kind="info">
          Your account is not activated yet, so searches are disabled. Add a licence key on the{" "}
          <a href="/activate" className="underline">
            activation page
          </a>
          .
        </Alert>
      ) : null}

      {error ? <Alert>{error}</Alert> : null}

      <form onSubmit={submit} className="space-y-6">
        <div>
          <div className="label">Source</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {SOURCES.map((item) => (
              <button
                type="button"
                key={item.id}
                onClick={() => setSource(item.id)}
                className={`rounded-xl border p-4 text-left transition-colors ${
                  source === item.id
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "bg-[var(--surface)] hover:bg-[var(--surface-2)]"
                }`}
              >
                <div className="font-medium">{item.title}</div>
                <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">{item.blurb}</p>
              </button>
            ))}
          </div>
        </div>

        <div className="card space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="keyword">
                Keyword / niche
              </label>
              <input
                id="keyword"
                className="field"
                required
                placeholder={source === "gmaps" ? "dentist" : "solar panel installer"}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="location">
                Location {activeSource.needsLocation || unlimited ? "" : "(optional)"}
              </label>
              <input
                id="location"
                className="field"
                required={activeSource.needsLocation}
                placeholder="Milan, Italy"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="label mb-0">How many leads</span>
              <button
                type="button"
                onClick={() => setUnlimited((u) => !u)}
                className={`chip border px-2.5 py-1 transition-colors ${
                  unlimited
                    ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "text-[var(--muted)] hover:bg-[var(--surface-2)]"
                }`}
              >
                ∞ Unlimited
              </button>
            </div>

            {unlimited ? (
              <div className="rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] p-3.5 text-sm">
                <p className="font-medium text-[var(--accent)]">
                  Everything the area has, no cap.
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-[var(--muted)]">
                  The area is split into a grid, and any cell that comes back busy is split again
                  into four and searched deeper — that is how a run gets past the roughly 120
                  results a single Maps search will ever return. It stops when the area is
                  exhausted, or when you stop it. Results save continuously, so stopping early
                  never loses what was already found.
                </p>
                {needsLocationForUnlimited ? (
                  <p className="mt-2 text-xs text-[var(--warn)]">
                    Add a location — an unlimited run needs an area to work through.
                  </p>
                ) : null}
              </div>
            ) : (
              <>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {PRESETS.map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setMaxLeads(n)}
                      className={`chip border px-2.5 py-1 transition-colors ${
                        maxLeads === n
                          ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                          : "text-[var(--muted)] hover:bg-[var(--surface-2)]"
                      }`}
                    >
                      {n}
                    </button>
                  ))}
                </div>
                <input
                  type="range"
                  min={10}
                  max={1000}
                  step={10}
                  value={maxLeads}
                  onChange={(e) => setMaxLeads(Number(e.target.value))}
                  className="w-full accent-[var(--accent)]"
                />
                <div className="mt-1 flex justify-between text-xs text-[var(--muted)]">
                  <span>10</span>
                  <span className="font-medium text-[var(--text)]">{maxLeads} leads</span>
                  <span>1000</span>
                </div>
                {source === "gmaps" && maxLeads > 90 ? (
                  <p className="mt-2 text-xs text-[var(--muted)]">
                    Above 90 the area is searched as a grid, because one Maps search only ever
                    returns about 120 results. A location is required for that.
                  </p>
                ) : null}
              </>
            )}
          </div>
        </div>

        <div>
          <div className="label">Email lookup</div>
          <div className="space-y-2">
            {ENRICH.map((item) => (
              <button
                type="button"
                key={item.id}
                onClick={() => setEnrichMode(item.id)}
                className={`flex w-full gap-3 rounded-xl border p-3 text-left transition-colors ${
                  enrichMode === item.id
                    ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                    : "bg-[var(--surface)] hover:bg-[var(--surface-2)]"
                }`}
              >
                <span
                  className={`mt-1 h-3.5 w-3.5 shrink-0 rounded-full border-2 ${
                    enrichMode === item.id
                      ? "border-[var(--accent)] bg-[var(--accent)]"
                      : "border-[var(--muted)]"
                  }`}
                />
                <span>
                  <span className="block text-sm font-medium">{item.title}</span>
                  <span className="mt-0.5 block text-xs text-[var(--muted)]">{item.blurb}</span>
                </span>
              </button>
            ))}
          </div>
          {enrichMode === "standard" && config ? (
            <p className="mt-2 text-xs text-[var(--muted)]">
              Reads up to {config.email_max_pages} pages per site, {config.email_concurrency} sites
              at a time. Around 5-6 in 10 businesses publish an address; the rest stay blank rather
              than guessed.
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            className="btn-primary flex-1"
            disabled={busy || !user?.is_active || needsLocationForUnlimited}
          >
            {busy ? <Spinner /> : null}
            {unlimited ? "Start unlimited search" : `Find up to ${maxLeads} leads`}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={saveSearch}
            disabled={saving || !keyword.trim()}
          >
            {saving ? <Spinner /> : null}
            Save search
          </button>
        </div>
      </form>

      {config ? (
        <p className="text-center text-xs text-[var(--muted)]">
          Search backend: {config.search_backend} · Maps detail workers: {config.gmaps_detail_workers}{" "}
          · Emails read from business websites
        </p>
      ) : null}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <SearchForm />
    </Suspense>
  );
}
