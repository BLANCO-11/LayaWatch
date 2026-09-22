/* Dialog and drawer. Contract: sections 4.19-4.20.
 * Widths 480/640, theme-correct scrim, serif title, focus trapped, Escape
 * cancels, initial focus on the least destructive control, focus restored.
 * Drawer: right panel 420px, full width under 640px, same focus rules. */
"use client";

import { useEffect, useRef } from "react";
import Button from "./Button";
import "./overlays.css";

function useTrap(open: boolean, onClose: () => void, initialRef: React.RefObject<HTMLElement | null>) {
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    initialRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !rootRef.current) return;
      const items = rootRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      const list = Array.from(items).filter((el) => !el.hasAttribute("disabled"));
      if (list.length === 0) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus();
    };
  }, [open, onClose, initialRef]);
  return rootRef;
}

export function Dialog({
  open,
  title,
  wide,
  confirmLabel,
  onConfirm,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  wide?: boolean;
  confirmLabel?: string;
  onConfirm?: () => void;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const initialRef = useRef<HTMLButtonElement>(null);
  const rootRef = useTrap(open, onClose, initialRef);
  if (!open) return null;
  return (
    <div className="lw-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={rootRef}
        className={`lw-dialog${wide ? " lw-dialog-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="lw-dialog-title">{title}</div>
        <div className="lw-dialog-body">{children}</div>
        <div className="lw-dialog-foot">
          <Button variant="ghost" onClick={onClose} ref={initialRef}>
            Cancel
          </Button>
          {confirmLabel ? (
            <Button variant={confirmLabel === "Revoke" || confirmLabel === "Delete" ? "danger" : "primary"} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const initialRef = useRef<HTMLButtonElement>(null);
  const rootRef = useTrap(open, onClose, initialRef);
  if (!open) return null;
  return (
    <div ref={rootRef} className="lw-drawer" role="dialog" aria-modal="true" aria-label={title}>
      <div className="lw-dialog-title">{title}</div>
      <div className="lw-dialog-body" style={{ flex: 1, overflowY: "auto" }}>
        {children}
      </div>
      <div className="lw-dialog-foot">
        <Button variant="ghost" onClick={onClose} ref={initialRef}>
          Close
        </Button>
      </div>
    </div>
  );
}

export default Dialog;
