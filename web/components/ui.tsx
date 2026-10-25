"use client";
import { useEffect, useRef, useState } from "react";
import { STATUS_COLOR, chLabel } from "@/lib/format";
import { useToast } from "./toast";

export function Badge({ status, plain, color }: { status: string; plain?: boolean; color?: string }) {
  return <span className={`badge ${color ?? STATUS_COLOR[status] ?? ""} ${plain ? "plain" : ""}`}>{status}</span>;
}
export const ChannelBadge = ({ channel }: { channel: string }) => <Badge status={chLabel(channel)} color="" />;
export const ActiveBadge = ({ active }: { active: boolean }) =>
  active ? <span className="badge green plain">active</span> : <span className="badge plain">inactive</span>;

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.3-4.3" /></svg>
      </div>
      <p className="empty-title">{title}</p>
      {hint && <p>{hint}</p>}
    </div>
  );
}

export function ShortId({ id }: { id?: string | null }) {
  const toast = useToast();
  if (!id) return <>—</>;
  return (
    <span className="mono copy-id" title={`${id} — click to copy`}
      onClick={e => { e.stopPropagation(); navigator.clipboard.writeText(id).then(() => toast("Copied to clipboard", "ok")); }}>
      {id.slice(0, 8)}…
    </span>
  );
}

export function Card({ title, sub, actions, children, flush, style }: {
  title?: React.ReactNode; sub?: React.ReactNode; actions?: React.ReactNode;
  children?: React.ReactNode; flush?: boolean; style?: React.CSSProperties;
}) {
  return (
    <div className="card" style={style}>
      {(title || actions) && (
        <div className="card-head">
          <div>{title && <div className="card-title">{title}</div>}{sub && <div className="card-sub">{sub}</div>}</div>
          {actions}
        </div>
      )}
      <div className={`card-body${flush ? " flush table-wrap" : ""}`}>{children}</div>
    </div>
  );
}

export const Field = ({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) => (
  <div className="field"><label>{label}</label>{children}{hint && <div className="secret-hint">{hint}</div>}</div>
);

export function Pager({ offset, count, total, page, onChange }: {
  offset: number; count: number; total?: number; page: number; onChange: (offset: number) => void;
}) {
  const to = offset + count;
  const hasNext = total !== undefined ? to < total : count >= page;
  return (
    <div className="card-body toolbar" style={{ justifyContent: "space-between" }}>
      <span className="muted">
        {count === 0 ? "No more rows" : `${offset + 1}–${to}${total !== undefined ? ` of ${total}` : ""}`}
      </span>
      <span>
        <button className="btn sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - page))}>Previous</button>{" "}
        <button className="btn sm" disabled={!hasNext} onClick={() => onChange(offset + page)}>Next</button>
      </span>
    </div>
  );
}

export function Modal({ title, onClose, onSubmit, submitLabel = "Save", width, children, footer = true }: {
  title: string; onClose: () => void; width?: string; submitLabel?: string; footer?: boolean;
  onSubmit?: (fd: FormData, form: HTMLFormElement) => Promise<void> | void; children: React.ReactNode;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button:not(.x-btn)")?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && ref.current) {
        const f = [...ref.current.querySelectorAll<HTMLElement>("input,select,textarea,button,a[href]")].filter(x => !x.hasAttribute("disabled"));
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} style={width ? { maxWidth: width } : undefined} ref={ref}>
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="x-btn" onClick={onClose} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <form style={{ display: "contents" }} onSubmit={async e => {
          e.preventDefault();
          if (!onSubmit) return;
          setBusy(true);
          try { await onSubmit(new FormData(e.currentTarget), e.currentTarget); onClose(); }
          catch (err) { toast((err as Error).message, "err"); }
          finally { setBusy(false); }
        }}>
          <div className="modal-body">{children}</div>
          {footer && (
            <div className="modal-foot">
              <button type="button" className="btn" onClick={onClose}>Cancel</button>
              {onSubmit && <button type="submit" className="btn primary" disabled={busy}>{submitLabel}</button>}
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

export function useConfirm() {
  return (msg: string) => typeof window !== "undefined" && window.confirm(msg);
}

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Clickable `<tr>` that is also reachable by keyboard. */
export function rowProps(onOpen: () => void) {
  return {
    className: "clickable-row", tabIndex: 0, role: "link" as const,
    onClick: onOpen,
    onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter") onOpen(); },
  };
}

export function PageIntro({ children }: { children: React.ReactNode }) {
  return <div className="muted" style={{ marginBottom: 14, fontSize: 13 }}>{children}</div>;
}
