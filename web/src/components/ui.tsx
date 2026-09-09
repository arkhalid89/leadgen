"use client";

import Link from "next/link";
import { useState } from "react";
import type { Quality } from "@/lib/types";

export function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
}) {
  const toneColor = {
    default: "text-[var(--text)]",
    good: "text-[var(--good)]",
    warn: "text-[var(--warn)]",
    bad: "text-[var(--bad)]",
  }[tone];

  return (
    <div className="card">
      <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <div className={`mt-2 text-3xl font-semibold tabular-nums ${toneColor}`}>{value}</div>
      {hint ? <div className="mt-1 text-xs text-[var(--muted)]">{hint}</div> : null}
    </div>
  );
}

export function QualityChip({ quality }: { quality: Quality }) {
  const style = {
    strong: "bg-[rgba(52,211,153,0.15)] text-[var(--good)]",
    medium: "bg-[rgba(251,191,36,0.15)] text-[var(--warn)]",
    weak: "bg-[rgba(138,151,177,0.15)] text-[var(--muted)]",
  }[quality] ?? "bg-[rgba(138,151,177,0.15)] text-[var(--muted)]";
  return <span className={`chip ${style}`}>{quality}</span>;
}

export function StatusChip({ status }: { status: string }) {
  const style =
    {
      completed: "bg-[rgba(52,211,153,0.15)] text-[var(--good)]",
      running: "bg-[rgba(79,124,255,0.18)] text-[var(--accent)]",
      queued: "bg-[rgba(138,151,177,0.15)] text-[var(--muted)]",
      stopped: "bg-[rgba(251,191,36,0.15)] text-[var(--warn)]",
      failed: "bg-[rgba(248,113,113,0.15)] text-[var(--bad)]",
    }[status] ?? "bg-[rgba(138,151,177,0.15)] text-[var(--muted)]";
  return <span className={`chip ${style}`}>{status}</span>;
}

export function ProgressBar({ value, animated = false }: { value: number; animated?: boolean }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
      <div
        className={`h-full rounded-full bg-[var(--accent)] transition-[width] duration-500 ${
          animated ? "animate-pulse" : ""
        }`}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
      aria-hidden
    />
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: { href: string; label: string };
}) {
  return (
    <div className="card flex flex-col items-center gap-3 py-14 text-center">
      <div className="text-base font-semibold">{title}</div>
      <p className="max-w-md text-sm text-[var(--muted)]">{body}</p>
      {action ? (
        <Link href={action.href} className="btn-primary mt-1">
          {action.label}
        </Link>
      ) : null}
    </div>
  );
}

export function Alert({
  kind = "error",
  children,
}: {
  kind?: "error" | "info" | "success";
  children: React.ReactNode;
}) {
  const style = {
    error: "border-[rgba(248,113,113,0.4)] bg-[rgba(248,113,113,0.1)] text-[var(--bad)]",
    info: "border-[rgba(79,124,255,0.4)] bg-[rgba(79,124,255,0.1)] text-[var(--accent)]",
    success: "border-[rgba(52,211,153,0.4)] bg-[rgba(52,211,153,0.1)] text-[var(--good)]",
  }[kind];
  return <div className={`rounded-lg border px-3 py-2 text-sm ${style}`}>{children}</div>;
}

/**
 * Shows every email or phone a business has, without letting one row with six
 * addresses stretch the whole table: the primary is inline, the rest expand.
 */
export function ContactList({
  values,
  primary,
  kind,
}: {
  values?: string[];
  primary: string;
  kind: "email" | "phone";
}) {
  const [open, setOpen] = useState(false);
  const all = values && values.length > 0 ? values : primary ? [primary] : [];

  if (all.length === 0) return <span className="text-[var(--muted)]">—</span>;

  const href = (value: string) =>
    kind === "email" ? `mailto:${value}` : `tel:${value.replace(/[^\d+]/g, "")}`;
  const extra = all.length - 1;

  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-1.5">
        <a href={href(all[0])} className="text-[var(--accent)] hover:underline">
          {all[0]}
        </a>
        {extra > 0 ? (
          <button
            onClick={() => setOpen((v) => !v)}
            title={all.slice(1).join("\n")}
            aria-expanded={open}
            className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--muted)] hover:text-[var(--text)]"
          >
            {open ? "−" : `+${extra}`}
          </button>
        ) : null}
      </span>
      {open
        ? all.slice(1).map((value) => (
            <a
              key={value}
              href={href(value)}
              className="text-xs text-[var(--accent)] hover:underline"
            >
              {value}
            </a>
          ))
        : null}
    </span>
  );
}

/** Shows how much an address can be trusted. */
export function SourceBadge({ source, status }: { source?: string; status?: string }) {
  if (status === "verified" && source !== "ai") {
    return (
      <span
        className="chip ml-1 bg-[rgba(52,211,153,0.15)] text-[var(--good)]"
        title={
          source === "pattern"
            ? "Guessed from a common pattern, then confirmed by the domain's mail server."
            : "Read off the business's website and confirmed by their mail server."
        }
      >
        ✓
      </span>
    );
  }
  if (source === "ai") {
    return (
      <span
        className="chip ml-1 bg-[rgba(251,191,36,0.15)] text-[var(--warn)]"
        title="Suggested by AI and not verified. Measured 30% precision — check before sending."
      >
        AI?
      </span>
    );
  }
  if (status === "catch_all") {
    return (
      <span
        className="chip ml-1 bg-[rgba(138,151,177,0.15)] text-[var(--muted)]"
        title="This domain accepts mail to any address, so the mailbox could not be confirmed either way."
      >
        catch-all
      </span>
    );
  }
  return null;
}

export function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  if (!href) return <span className="text-[var(--muted)]">—</span>;
  const url = href.startsWith("http") ? href : `https://${href}`;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[var(--accent)] hover:underline"
    >
      {children}
    </a>
  );
}

export function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value.includes("T") ? value : value.replace(" ", "T") + "Z");
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
