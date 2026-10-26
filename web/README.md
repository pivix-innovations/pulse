# Pulse Console (Next.js)

The operator console for Pulse: Next.js App Router, React 19, TypeScript, TanStack Query.
It is deployed separately from the API (the old `ui/index.html` console is gone) and talks to it only over HTTP.

```bash
make web-install   # once
make api           # Pulse API on :9000 (or make start for the whole stack)
make web           # console on http://localhost:3100
```

`next.config.ts` rewrites `/api/*` and `/health` to the Pulse API (`PULSE_API_ORIGIN`, default
`http://localhost:9000`), so the browser stays same-origin: the session cookie, `X-Tenant-Id` and the
SSE stream work without CORS.

### Docker image

[Dockerfile](Dockerfile) is multi-stage: Node builds the site as static files (`STATIC_EXPORT=1`), and the final image is
`nginx-unprivileged` serving them (about 60 MB, against about 210 MB for the old Node standalone server). nginx does what
the Next rewrites do in development: it proxies `/api/*` and `/health` to `PULSE_API_ORIGIN`, which is read when the
container starts, so one image works for any environment:

```sh
docker build -t pulse-web ./web
docker run -p 3100:3100 -e PULSE_API_ORIGIN=https://dev-pulse-api.pivix.in pulse-web
```

## Layout
- `app/login` — sign-in. `app/(console)/*` — one folder per page, behind the auth shell.
- `lib/api.ts` — fetch client (tenant header, 401 handling, `X-Total-Count` paging). `lib/auth.tsx` — session and acting tenant.
- `lib/hooks.ts` — `useTq` (tenant-scoped query) and `useAct` (mutation + toast + invalidation).
- `components/` — UI kit (`ui.tsx`), shell, toast, workflow editor. Styling is the legacy design tokens in `app/globals.css`.
- `lib/routes.ts` — the sidebar and page titles; add a page by adding a folder and a row here.

New compared with the legacy console: **Inbox** (`/inbox`) and **Events** (`/events`).
