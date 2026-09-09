"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  push: (message: string, kind?: ToastKind) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const id = nextId++;
    setToasts((prev) => [...prev, { id, kind, message }]);
    // Errors stay long enough to read and act on; confirmations get out of the way.
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, kind === "error" ? 7000 : 3800);
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (m: string) => push(m, "success"),
      error: (m: string) => push(m, "error"),
      info: (m: string) => push(m, "info"),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="status"
            className={`pointer-events-auto flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-sm shadow-lg backdrop-blur ${
              {
                success:
                  "border-[rgba(52,211,153,0.4)] bg-[rgba(16,44,36,0.95)] text-[var(--good)]",
                error: "border-[rgba(248,113,113,0.4)] bg-[rgba(50,20,20,0.95)] text-[var(--bad)]",
                info: "border-[rgba(79,124,255,0.4)] bg-[rgba(18,26,48,0.95)] text-[var(--text)]",
              }[toast.kind]
            }`}
          >
            <span className="mt-0.5 shrink-0">
              {toast.kind === "success" ? "✓" : toast.kind === "error" ? "!" : "i"}
            </span>
            <span className="min-w-0 flex-1 break-words">{toast.message}</span>
            <button
              className="shrink-0 opacity-60 hover:opacity-100"
              onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
