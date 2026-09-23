/* Dropdown menu. Contract: design-language section 4.18.
 * Anchored raised popover, 30px items, destructive items in err, closes on
 * select/Escape/outside click, arrows plus Home/End, focus restore. */
"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import "./overlays.css";

/* Layout effect, client only: SSR renders without window. */
const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

export interface MenuItem {
  key: string;
  label: string;
  danger?: boolean;
}

export default function Dropdown({
  trigger,
  items,
  onSelect,
  label,
}: {
  trigger: React.ReactNode;
  items: MenuItem[];
  onSelect: (key: string) => void;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open ]);

  useEffect(() => {
    if (open) setFocus(0);
    else triggerRef.current?.focus();
  }, [open]);

  /* Place synchronously after layout: right-aligned by CSS, flip above the
   * trigger when the viewport bottom would cut the menu off, else clamp. */
  useIsoLayoutEffect(() => {
    if (!open) return;
    const pop = popRef.current;
    if (!pop) return;
    const place = () => {
      pop.style.top = "";
      pop.style.bottom = "";
      if (pop.getBoundingClientRect().bottom > window.innerHeight - 8) {
        pop.style.top = "auto";
        pop.style.bottom = "calc(100% + 6px)";
        if (pop.getBoundingClientRect().top < 8 && wrapRef.current) {
          /* Neither edge fits (max-height already clamps): pin to viewport. */
          pop.style.bottom = "";
          pop.style.top = `${8 - wrapRef.current.getBoundingClientRect().top}px`;
        }
      }
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
        style={{ background: "none", border: 0, padding: 0, cursor: "pointer", color: "inherit" }}
      >
        {trigger}
      </button>
      {open ? (
        <div ref={popRef} className="lw-pop" role="menu" id={menuId} aria-label={label}>
          {items.map((item, i) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              data-focus={i === focus}
              tabIndex={i === focus ? 0 : -1}
              className={`lw-pop-item${item.danger ? " lw-pop-item-danger" : ""}`}
              onClick={() => {
                setOpen(false);
                onSelect(item.key);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  const next = (i + 1) % items.length;
                  setFocus(next);
                  wrapRef.current
                    ?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')
                    [next]?.focus();
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  const next = (i - 1 + items.length) % items.length;
                  setFocus(next);
                  wrapRef.current
                    ?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')
                    [next]?.focus();
                } else if (e.key === "Home") {
                  e.preventDefault();
                  setFocus(0);
                  wrapRef.current
                    ?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[0]
                    ?.focus();
                } else if (e.key === "End") {
                  e.preventDefault();
                  setFocus(items.length - 1);
                  wrapRef.current
                    ?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')
                    [items.length - 1]?.focus();
                }
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
