"use client";
import { useToast } from "./toast";

export function RevealBox({ children, copy }: { children: React.ReactNode; copy?: string }) {
  const toast = useToast();
  return (
    <div className="reveal-box" role="status">
      {children}
      {copy && (
        <button className="btn sm" style={{ marginTop: 8 }} onClick={async () => {
          try { await navigator.clipboard.writeText(copy); toast("Copied", "ok"); } catch { toast("Copy failed — select it manually", "err"); }
        }}>Copy</button>)}
    </div>
  );
}
