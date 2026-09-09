"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ServerConfig } from "@/lib/types";
import { Alert, Spinner, formatDate } from "@/components/ui";
import { useToast } from "@/lib/toast";
import type { SuppressionEntry } from "@/lib/types";

export default function SettingsPage() {
  const { user, refresh, logout } = useAuth();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [suppression, setSuppression] = useState<SuppressionEntry[]>([]);
  const [suppressValue, setSuppressValue] = useState("");
  const toast = useToast();
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    setFullName(user?.full_name ?? "");
  }, [user]);

  useEffect(() => {
    api.get<ServerConfig>("/api/dashboard/config").then(setConfig).catch(() => {});
    void loadSuppression();
  }, []);

  async function loadSuppression() {
    try {
      const data = await api.get<{ entries: SuppressionEntry[] }>("/api/suppression");
      setSuppression(data.entries);
    } catch {
      /* not fatal */
    }
  }

  async function addSuppression(event: React.FormEvent) {
    event.preventDefault();
    const values = suppressValue
      .split(/[\s,;]+/)
      .map((v) => v.trim())
      .filter(Boolean);
    if (!values.length) return;
    await api.post("/api/suppression", { values, reason: "Added manually" });
    setSuppressValue("");
    await loadSuppression();
    toast.success(`${values.length} added to do-not-contact`);
  }

  async function saveProfile(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy("profile");
    try {
      await api.put("/api/account/profile", { full_name: fullName });
      await refresh();
      setNotice("Profile updated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update profile");
    } finally {
      setBusy("");
    }
  }

  async function savePassword(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy("password");
    try {
      await api.put("/api/account/password", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setNotice("Password changed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password");
    } finally {
      setBusy("");
    }
  }

  async function deleteAccount() {
    if (!confirm("Delete your account and every lead in it? This cannot be undone.")) return;
    if (!confirm("Last chance — this permanently erases all your data. Continue?")) return;
    await api.del("/api/account");
    await logout();
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Manage your account and see how the server is configured.</p>
      </div>

      {error ? <Alert>{error}</Alert> : null}
      {notice ? <Alert kind="success">{notice}</Alert> : null}

      <form onSubmit={saveProfile} className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Profile
        </h2>
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <input id="email" className="field opacity-60" value={user?.email ?? ""} disabled />
        </div>
        <div>
          <label className="label" htmlFor="fullname">
            Full name
          </label>
          <input
            id="fullname"
            className="field"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
          />
        </div>
        <button className="btn-primary" disabled={busy === "profile"}>
          {busy === "profile" ? <Spinner /> : null}
          Save profile
        </button>
      </form>

      <form onSubmit={savePassword} className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Password
        </h2>
        <div>
          <label className="label" htmlFor="current">
            Current password
          </label>
          <input
            id="current"
            type="password"
            className="field"
            required
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="new">
            New password
          </label>
          <input
            id="new"
            type="password"
            className="field"
            required
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </div>
        <button className="btn-primary" disabled={busy === "password"}>
          {busy === "password" ? <Spinner /> : null}
          Change password
        </button>
      </form>

      <div className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Licence
        </h2>
        {user?.is_active ? (
          <p className="text-sm">
            Active — <span className="font-mono text-[var(--good)]">{user.license_key}</span>
          </p>
        ) : (
          <p className="text-sm text-[var(--warn)]">
            Not activated.{" "}
            <a href="/activate" className="underline">
              Enter a licence key
            </a>{" "}
            to enable searching.
          </p>
        )}
      </div>

      <div className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Server configuration
        </h2>
        {config ? (
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-[var(--muted)]">Email source</dt>
            <dd className="text-[var(--good)]">
              Business websites — free, no API key
            </dd>
            <dt className="text-[var(--muted)]">Pages read per site</dt>
            <dd className="tabular-nums">{config.email_max_pages}</dd>
            <dt className="text-[var(--muted)]">Sites fetched in parallel</dt>
            <dd className="tabular-nums">{config.email_concurrency}</dd>
            <dt className="text-[var(--muted)]">SMTP verification</dt>
            <dd className={config.smtp_verify_enabled ? "text-[var(--good)]" : "text-[var(--muted)]"}>
              {config.smtp_verify_enabled ? "Enabled" : "Off — needs outbound port 25"}
            </dd>
            <dt className="text-[var(--muted)]">Outreach copy (optional)</dt>
            <dd className={config.gemini_configured ? "text-[var(--good)]" : "text-[var(--muted)]"}>
              {config.gemini_configured ? config.gemini_model : "No key — built-in templates"}
            </dd>
            <dt className="text-[var(--muted)]">Web search backend</dt>
            <dd>{config.search_backend}</dd>
            <dt className="text-[var(--muted)]">Maps detail workers</dt>
            <dd className="tabular-nums">{config.gmaps_detail_workers}</dd>
            <dt className="text-[var(--muted)]">Concurrent searches allowed</dt>
            <dd className="tabular-nums">{config.max_active_jobs}</dd>
          </dl>
        ) : (
          <Spinner />
        )}
        <p className="text-xs text-[var(--muted)]">
          These come from environment variables on the server. See <code>.env.example</code>.
        </p>
      </div>

      <div className="card space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted)]">
          Do-not-contact list
        </h2>
        <p className="text-sm text-[var(--muted)]">
          Addresses and domains here are excluded from every CSV export. Add anyone who
          unsubscribes, bounces, or asks not to be contacted.
        </p>
        <form onSubmit={addSuppression} className="flex gap-2">
          <input
            className="field"
            placeholder="email@example.com, competitor.com"
            value={suppressValue}
            onChange={(e) => setSuppressValue(e.target.value)}
          />
          <button className="btn-ghost shrink-0">Add</button>
        </form>
        {suppression.length ? (
          <ul className="max-h-56 space-y-1 overflow-y-auto">
            {suppression.map((entry) => (
              <li
                key={entry.id}
                className="flex items-center justify-between gap-2 rounded border bg-[var(--surface-2)] px-2.5 py-1.5 text-sm"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{entry.value}</span>
                <span className="shrink-0 text-xs text-[var(--muted)]">
                  {formatDate(entry.created_at)}
                </span>
                <button
                  className="shrink-0 text-[var(--muted)] hover:text-[var(--bad)]"
                  onClick={async () => {
                    await api.del(`/api/suppression/${entry.id}`);
                    await loadSuppression();
                  }}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-[var(--muted)]">Nothing suppressed yet.</p>
        )}
      </div>

      <div className="card space-y-3 border-[rgba(248,113,113,0.35)]">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--bad)]">
          Danger zone
        </h2>
        <p className="text-sm text-[var(--muted)]">
          Deleting your account removes every lead, search and draft you own.
        </p>
        <button className="btn-danger" onClick={deleteAccount}>
          Delete account
        </button>
      </div>
    </div>
  );
}
