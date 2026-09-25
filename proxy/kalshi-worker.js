// Read-only relay for Kalshi's public market data (a Cloudflare Worker).
//
// Kalshi's API refuses requests that carry a browser Origin header, so a web page
// cannot read Kalshi prices directly. This Worker forwards GET requests for
// market and event data to Kalshi without the Origin header and returns the
// response with CORS headers. Responses are cached for 10 seconds at the edge, so
// any number of open pages cost Kalshi at most one request per URL every 10 s.
//
// Deploy (free Cloudflare account): Workers & Pages -> Create -> Worker ->
// paste this file -> Deploy. Put the Worker's URL in site/data/live_config.json
// as "kalshi_proxy". See README, "Live Kalshi prices".

const UPSTREAM = "https://api.elections.kalshi.com";
const ALLOWED_PATH = /^\/trade-api\/v2\/(markets|events)(\/[A-Za-z0-9._-]+)?$/;
const ALLOWED_ORIGINS = ["https://mahmedken.github.io"];
const LOCAL = /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;
const CACHE_SECONDS = 10;

function corsHeaders(origin) {
  const ok = ALLOWED_ORIGINS.includes(origin) || LOCAL.test(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const cors = corsHeaders(request.headers.get("Origin") || "");
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "GET" || !ALLOWED_PATH.test(url.pathname)) {
      return new Response("Only GET /trade-api/v2/markets and /trade-api/v2/events are relayed.", { status: 403, headers: cors });
    }
    const upstream = new Request(UPSTREAM + url.pathname + url.search, { headers: { Accept: "application/json" } });
    const cache = typeof caches !== "undefined" ? caches.default : null;
    let res = cache ? await cache.match(upstream) : null;
    if (!res) {
      const r = await fetch(upstream);
      res = new Response(r.body, { status: r.status, headers: { "Content-Type": "application/json", "Cache-Control": `public, max-age=${CACHE_SECONDS}` } });
      if (cache && r.ok) ctx.waitUntil(cache.put(upstream, res.clone()));
    }
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
    return out;
  },
};
