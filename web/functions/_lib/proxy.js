// Forward a request to the Pulse API so the browser stays same-origin
// (the session cookie and SSE stay same-origin, no CORS).
export async function proxy(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  // FastAPI serves /health/ (trailing slash) and would redirect /health to the API host.
  const path = url.pathname === "/health" ? "/health/" : url.pathname;
  const target = new URL(path + url.search, env.API_ORIGIN);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("x-forwarded-host", url.host);
  const upstream = await fetch(new Request(target, { method: request.method, headers, body: request.body, redirect: "manual" }));
  return new Response(upstream.body, upstream);
}
