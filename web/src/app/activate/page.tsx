"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { Alert, Spinner } from "@/components/ui";

export default function ActivatePage() {
  const { user, activate } = useAuth();
  const router = useRouter();
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await activate(key.trim());
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Activation failed");
    } finally {
      setBusy(false);
    }
  }

  if (user?.is_active) {
    return (
      <div className="mx-auto max-w-lg">
        <h1 className="mb-1 text-2xl font-semibold">Account activated</h1>
        <p className="mb-6 text-sm text-[var(--muted)]">
          Licence key <span className="font-mono">{user.license_key}</span> is active on this
          account.
        </p>
        <button className="btn-primary" onClick={() => router.push("/search")}>
          Start finding leads
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg">
      <h1 className="mb-1 text-2xl font-semibold">Activate your account</h1>
      <p className="mb-6 text-sm text-[var(--muted)]">
        Enter a licence key to unlock lead searching. Everything else — your dashboard, database
        and outreach drafts — works without one.
      </p>

      <form onSubmit={submit} className="card space-y-4">
        {error ? <Alert>{error}</Alert> : null}
        <div>
          <label className="label" htmlFor="key">
            Licence key
          </label>
          <input
            id="key"
            className="field font-mono uppercase"
            placeholder="LEAD-XXXX-XXXX-XXXX"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            required
          />
        </div>
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? <Spinner /> : null}
          Activate
        </button>
      </form>
    </div>
  );
}
