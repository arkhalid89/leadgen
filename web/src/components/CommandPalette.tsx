"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { Lead } from "@/lib/types";

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
}

/**
 * ⌘K / Ctrl-K palette: jump anywhere, or find a lead by name, without
 * reaching for the mouse. Lead search is debounced and hits the indexed
 * full-text endpoint, so it stays responsive on a large database.
 */
export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [leads, setLeads] = useState<Lead[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      setLeads([]);
      window.setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  // Debounced lead lookup.
  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setLeads([]);
      return;
    }
    const timer = window.setTimeout(() => {
      api
        .get<{ leads: Lead[] }>(`/api/leads?search=${encodeURIComponent(query)}&limit=6`)
        .then((data) => setLeads(data.leads))
        .catch(() => setLeads([]));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [query, open]);

  const go = useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href);
    },
    [router],
  );

  const commands = useMemo<Command[]>(() => {
    const nav: Command[] = [
      { id: "dash", label: "Dashboard", group: "Go to", run: () => go("/") },
      { id: "find", label: "Find Leads", hint: "new search", group: "Go to", run: () => go("/search") },
      { id: "jobs", label: "Searches", group: "Go to", run: () => go("/jobs") },
      { id: "leads", label: "Lead Database", group: "Go to", run: () => go("/leads") },
      { id: "lists", label: "Lists", group: "Go to", run: () => go("/lists") },
      { id: "analytics", label: "Analytics", group: "Go to", run: () => go("/analytics") },
      { id: "outreach", label: "Outreach", group: "Go to", run: () => go("/outreach") },
      { id: "settings", label: "Settings", group: "Go to", run: () => go("/settings") },
      {
        id: "new-gmaps",
        label: "New Google Maps search",
        group: "Actions",
        run: () => go("/search?source=gmaps"),
      },
      {
        id: "new-web",
        label: "New web search",
        group: "Actions",
        run: () => go("/search?source=websearch"),
      },
      {
        id: "with-email",
        label: "Leads with an email",
        group: "Actions",
        run: () => go("/leads?has_email=true"),
      },
      {
        id: "verified",
        label: "Verified mailboxes only",
        group: "Actions",
        run: () => go("/leads?verified=true"),
      },
    ];

    const term = query.trim().toLowerCase();
    const filtered = term
      ? nav.filter((c) => c.label.toLowerCase().includes(term) || c.group.toLowerCase().includes(term))
      : nav;

    const leadCommands: Command[] = leads.map((lead) => ({
      id: `lead-${lead.id}`,
      label: lead.business_name || "(no name)",
      hint: lead.email || lead.phone || lead.website,
      group: "Leads",
      run: () => go(`/leads?focus=${lead.id}`),
    }));

    return [...filtered, ...leadCommands];
  }, [query, leads, go]);

  useEffect(() => {
    setCursor((c) => Math.min(c, Math.max(0, commands.length - 1)));
  }, [commands.length]);

  if (!open) return null;

  const grouped: Record<string, Command[]> = {};
  commands.forEach((c) => {
    (grouped[c.group] ||= []).push(c);
  });
  let index = -1;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-black/60 p-4 pt-[12vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border bg-[var(--surface)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="w-full border-b bg-transparent px-4 py-3.5 text-sm outline-none placeholder:text-[var(--muted)]"
          placeholder="Search leads, or jump to a page…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => Math.min(c + 1, commands.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) => Math.max(c - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              commands[cursor]?.run();
            }
          }}
        />
        <div className="max-h-[52vh] overflow-y-auto p-1.5">
          {commands.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-[var(--muted)]">Nothing matches.</p>
          ) : (
            Object.entries(grouped).map(([group, items]) => (
              <div key={group} className="mb-1">
                <div className="px-3 py-1.5 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--muted)]">
                  {group}
                </div>
                {items.map((command) => {
                  index += 1;
                  const active = index === cursor;
                  const position = index;
                  return (
                    <button
                      key={command.id}
                      onMouseEnter={() => setCursor(position)}
                      onClick={command.run}
                      className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm ${
                        active ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "hover:bg-[var(--surface-2)]"
                      }`}
                    >
                      <span className="truncate">{command.label}</span>
                      {command.hint ? (
                        <span className="shrink-0 truncate text-xs text-[var(--muted)]">
                          {command.hint}
                        </span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        <div className="flex items-center gap-3 border-t px-4 py-2 text-[0.68rem] text-[var(--muted)]">
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}
