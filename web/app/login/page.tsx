"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";

function LoginForm() {
  const { login, me, loading } = useAuth();
  const router = useRouter();
  const expired = useSearchParams().get("expired");
  const [error, setError] = useState<string | null>(expired ? "Your session has expired — sign in again." : null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!loading && me) router.replace("/overview"); }, [loading, me, router]);

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <div className="brand-mark">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12h4l3-8 4 16 3-8h6" /></svg>
          </div>
          <div><div className="brand-name">Pulse</div><div className="brand-sub">Notification Console</div></div>
        </div>
        {error && <div className="login-error" role="alert">{error}</div>}
        <form onSubmit={async e => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          setBusy(true);
          try { await login(String(fd.get("email")), String(fd.get("password"))); router.replace("/overview"); }
          catch (err) { setError((err as Error).message); }
          finally { setBusy(false); }
        }}>
          <div className="field"><label htmlFor="email">Email</label><input id="email" name="email" type="email" required autoComplete="username" placeholder="you@company.com" autoFocus /></div>
          <div className="field"><label htmlFor="password">Password</label><input id="password" name="password" type="password" required autoComplete="current-password" /></div>
          <button className="btn primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center" }}>Sign in</button>
        </form>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
