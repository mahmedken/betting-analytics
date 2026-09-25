// Offline tests for the Kalshi relay. Run: node --test proxy/*.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import worker from "./kalshi-worker.js";

const ctx = { waitUntil: () => {} };
const withFetch = async (fn) => {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (req) => { calls.push(req); return new Response(JSON.stringify({ markets: [{ ticker: "T", yes_bid_dollars: "0.24" }] }), { status: 200 }); };
  try { return await fn(calls); } finally { globalThis.fetch = real; }
};

test("relays market reads without the browser Origin and adds CORS", async () => {
  await withFetch(async (calls) => {
    const res = await worker.fetch(new Request("https://relay.example/trade-api/v2/markets?tickers=A,B", { headers: { Origin: "https://mahmedken.github.io" } }), {}, ctx);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://mahmedken.github.io");
    assert.equal((await res.json()).markets[0].ticker, "T");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "https://api.elections.kalshi.com/trade-api/v2/markets?tickers=A,B");
    assert.equal(calls[0].headers.get("Origin"), null);
  });
});

test("answers preflight and refuses anything but market and event reads", async () => {
  await withFetch(async (calls) => {
    const pre = await worker.fetch(new Request("https://relay.example/trade-api/v2/markets", { method: "OPTIONS", headers: { Origin: "http://localhost:8000" } }), {}, ctx);
    assert.equal(pre.status, 204);
    assert.equal(pre.headers.get("Access-Control-Allow-Origin"), "http://localhost:8000");
    for (const [path, method] of [["/trade-api/v2/portfolio/balance", "GET"], ["/trade-api/v2/markets", "POST"], ["/anything", "GET"]]) {
      const r = await worker.fetch(new Request(`https://relay.example${path}`, { method }), {}, ctx);
      assert.equal(r.status, 403, path + " " + method);
    }
    assert.equal(calls.length, 0);
  });
});
