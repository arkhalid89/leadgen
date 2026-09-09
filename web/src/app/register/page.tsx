"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { Alert, Spinner } from "@/components/ui";

export default function RegisterPage() {
  const { register, user, loading } = useAuth();
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && user) router.replace("/");
  }, [loading, user, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await register(email.trim(), password, fullName.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-5">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-[var(--accent)] text-lg font-bold text-white">
            L
          </div>
          <h1 className="text-xl font-semibold">Create your account</h1>
        </div>

        <form onSubmit={submit} className="card space-y-4">
          {error ? <Alert>{error}</Alert> : null}
          <div>
            <label className="label" htmlFor="name">
              Full name
            </label>
            <input
              id="name"
              className="field"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              className="field"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="new-password"
              className="field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="mt-1.5 text-xs text-[var(--muted)]">
              At least 8 characters, with a letter and a number.
            </p>
          </div>
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? <Spinner /> : null}
            Create account
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-[var(--muted)]">
          Already registered?{" "}
          <Link href="/login" className="text-[var(--accent)] hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
