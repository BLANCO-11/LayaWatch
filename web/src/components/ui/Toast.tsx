/* Toasts. Contract: design-language section 4.21.
 * Bottom-right stack capped at 3, severity left accent bar, mono title plus
 * one body line, 6s auto-dismiss with errors persisting, announced through
 * aria-live="polite", never carries secret values. */
"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";
import "./overlays.css";

export type ToastTone = "info" | "ok" | "warn" | "err";

export interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  body?: string;
}

const ToastCtx = createContext<{ push: (t: Omit<ToastItem, "id">) => void }>({
  push: () => {},
});

export function useToast(): { push: (t: Omit<ToastItem, "id">) => void } {
  return useContext(ToastCtx);
}

function toneClass(tone: ToastTone): string {
  if (tone === "ok") return "lw-toast lw-toast-ok";
  if (tone === "warn") return "lw-toast lw-toast-warn";
  if (tone === "err") return "lw-toast lw-toast-err";
  return "lw-toast";
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = nextId.current++;
    setItems((prev) => [...prev.slice(-2), { ...t, id }]);
    if (t.tone !== "err") {
      window.setTimeout(() => {
        setItems((prev) => prev.filter((item) => item.id !== id));
      }, 6000);
    }
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="lw-toasts" aria-live="polite" aria-label="Notifications">
        {items.map((item) => (
          <div key={item.id} className={toneClass(item.tone)} role="status">
            <div className="lw-toast-title">{item.title}</div>
            {item.body ? <div className="lw-toast-body">{item.body}</div> : null}
            <button
              type="button"
              className="lw-tag-x"
              onClick={() => dismiss(item.id)}
              aria-label={`Dismiss ${item.title}`}
              style={{ marginTop: 4 }}
            >
              Dismiss
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export default ToastProvider;
