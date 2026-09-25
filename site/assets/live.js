// Live data polled from the browser between server runs.
//
//   ESPN scoreboard   scores, match clock, goals and red cards   every 15 s while a match is on, else 2 min
//   ESPN summary      DraftKings odds for matches within 48 h    every 2 min
//   Polymarket CLOB   order books (best bid / ask and size)      every 30 s
//   Kalshi            best bid / ask, through a relay            every 30 s (Kalshi rejects browser requests)
//   meta.json         a new server run (model, simulations)      every 5 min
//
// Pages declare what they show with need(); polling pauses while the tab is hidden.
import { bestOfBook, americanToDecimal } from "./quant.js";

const ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer";
const CLOB = "https://clob.polymarket.com";
const FAST = 15e3, SLOW = 120e3, PRICES = 30e3, ODDS = 120e3, META = 300e3;

export const live = {
  events: new Map(),   // ESPN event id -> {state, name, clock, hg, ag, goals:[{side,minute}], reds:{home,away}, t}
  odds: new Map(),     // ESPN event id -> {provider, odds:[h,d,a], ou_line, odds_over, odds_under, t}
  poly: new Map(),     // Polymarket token -> {bid, ask, bidSize, askSize, t}
  kalshi: new Map(),   // Kalshi ticker -> {bid, ask, t}
  status: {},          // source -> {ok, t, error, fails}
  proxy: null,         // Kalshi relay base URL (site/data/live_config.json)
};

let needs = { espn: [], odds: [], poly: [], kalshi: [], kickoffs: [] };
let due = {};
let onChange = () => {}, onServer = () => {}, serverVersion = null;
let changeTimer = null;
const changed = () => { clearTimeout(changeTimer); changeTimer = setTimeout(() => onChange(), 250); };

export function start({ proxy, version, update, server }) {
  live.proxy = proxy || null;
  serverVersion = version;
  onChange = update;
  onServer = server;
  setInterval(tick, 5e3);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { due = {}; tick(); } });
}

// Pages call this after rendering. espn: [{league, date: YYYYMMDD}], odds: [{league, id}],
// poly: [token], kalshi: [ticker], kickoffs: [ISO time] (speeds up polling near kick-off).
export function need(n) {
  const uniq = (xs, key = (x) => x) => [...new Map(xs.filter(Boolean).map((x) => [key(x), x])).values()];
  const next = {
    espn: uniq(n.espn || [], (x) => `${x.league}|${x.date}`), odds: uniq(n.odds || [], (x) => x.id),
    poly: uniq(n.poly || []), kalshi: uniq(n.kalshi || []), kickoffs: n.kickoffs || [],
  };
  const grew = ["espn", "odds", "poly", "kalshi"].filter((k) => next[k].some((x) => !needs[k].some((y) => JSON.stringify(y) === JSON.stringify(x))));
  needs = next;
  grew.forEach((k) => { due[k] = 0; });
  if (grew.length) tick();
}

// ESPN's scoreboard dates follow US Eastern time.
const etDate = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
export const espnDate = (iso) => etDate.format(new Date(iso)).replace(/-/g, "");

function anyLiveSoon() {
  const now = Date.now();
  if ([...live.events.values()].some((e) => e.state === "in")) return true;
  return needs.kickoffs.some((k) => { const d = new Date(k) - now; return d < 10 * 60e3 && d > -150 * 60e3; });
}

async function run(source, fn, interval) {
  const st = (live.status[source] ||= { fails: 0 });
  if (document.hidden || (due[source] || 0) > Date.now() || st.busy) return;
  st.busy = true;
  try {
    const n = await fn();
    st.ok = true; st.t = Date.now(); st.fails = 0; st.error = null; st.n = n;
  } catch (e) {
    st.ok = false; st.error = String(e.message || e); st.fails++;
  } finally {
    st.busy = false;
    due[source] = Date.now() + interval() * Math.min(2 ** st.fails, 20);
  }
}

function tick() {
  if (needs.espn.length) run("espn", pollEspn, () => (anyLiveSoon() ? FAST : SLOW));
  if (needs.odds.length) run("odds", pollOdds, () => ODDS);
  if (needs.poly.length) run("poly", pollPoly, () => PRICES);
  if (needs.kalshi.length && live.proxy) run("kalshi", pollKalshi, () => PRICES);
  run("meta", pollMeta, () => META);
}

async function getJson(url, init) {
  const r = await fetch(url, { cache: "no-store", ...init });
  if (!r.ok) throw new Error(`${new URL(url).host} ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
async function pollEspn() {
  let n = 0, diff = false;
  for (const { league, date } of needs.espn) {
    const d = await getJson(`${ESPN}/${league}/scoreboard?dates=${date}`);
    for (const e of d.events || []) {
      const c = e.competitions[0];
      const side = Object.fromEntries(c.competitors.map((x) => [x.team.id, x.homeAway]));
      const sc = Object.fromEntries(c.competitors.map((x) => [x.homeAway, +x.score || 0]));
      const goals = [], reds = { home: 0, away: 0 };
      for (const x of c.details || []) {
        const s = side[x.team?.id];
        if (!s) continue;
        if (x.scoringPlay) goals.push({ side: s, minute: x.clock?.displayValue || "" });
        if (x.redCard) reds[s]++;
      }
      const rec = { state: e.status.type.state, name: e.status.type.name, detail: e.status.type.shortDetail,
        status: e.status, clock: e.status.displayClock, hg: sc.home, ag: sc.away, goals, reds };
      const old = live.events.get(e.id);
      if (!old || old.state !== rec.state || old.clock !== rec.clock || old.hg !== rec.hg || old.ag !== rec.ag || old.reds.home + old.reds.away !== reds.home + reds.away) diff = true;
      live.events.set(e.id, { ...rec, t: Date.now() });
      n++;
    }
  }
  if (diff) changed();
  return n;
}

async function pollOdds() {
  let n = 0, diff = false;
  for (const { league, id } of needs.odds.slice(0, 16)) {
    const ev = live.events.get(id);
    if (ev && ev.state !== "pre") continue;
    const d = await getJson(`${ESPN}/${league}/summary?event=${id}`);
    const o = (d.pickcenter || d.odds || [])[0];
    if (!o) continue;
    const odds = [americanToDecimal(o.homeTeamOdds?.moneyLine), americanToDecimal(o.drawOdds?.moneyLine), americanToDecimal(o.awayTeamOdds?.moneyLine)];
    if (odds.some((x) => !isFinite(x))) continue;
    const old = live.odds.get(id);
    if (!old || old.odds.some((x, i) => Math.abs(x - odds[i]) > 1e-9)) diff = true;
    live.odds.set(id, { provider: o.provider?.name, odds, ou_line: o.overUnder, odds_over: americanToDecimal(o.overOdds), odds_under: americanToDecimal(o.underOdds), t: Date.now() });
    n++;
  }
  if (diff) changed();
  return n;
}

async function pollPoly() {
  let n = 0, diff = false;
  for (let i = 0; i < needs.poly.length; i += 50) {
    const body = JSON.stringify(needs.poly.slice(i, i + 50).map((t) => ({ token_id: t })));
    const books = await getJson(`${CLOB}/books`, { method: "POST", headers: { "Content-Type": "application/json" }, body });
    for (const b of books) {
      const q = bestOfBook(b);
      const old = live.poly.get(b.asset_id);
      if (!old || old.bid !== q.bid || old.ask !== q.ask) diff = true;
      live.poly.set(b.asset_id, { ...q, t: Date.now() });
      n++;
    }
  }
  if (diff) changed();
  return n;
}

async function pollKalshi() {
  let n = 0, diff = false;
  for (let i = 0; i < needs.kalshi.length; i += 100) {
    const d = await getJson(`${live.proxy.replace(/\/$/, "")}/trade-api/v2/markets?tickers=${encodeURIComponent(needs.kalshi.slice(i, i + 100).join(","))}`);
    for (const m of d.markets || []) {
      const q = { bid: m.yes_bid_dollars != null ? +m.yes_bid_dollars : null, ask: m.yes_ask_dollars != null ? +m.yes_ask_dollars : null };
      const old = live.kalshi.get(m.ticker);
      if (!old || old.bid !== q.bid || old.ask !== q.ask) diff = true;
      live.kalshi.set(m.ticker, { ...q, t: Date.now() });
      n++;
    }
  }
  if (diff) changed();
  return n;
}

async function pollMeta() {
  const m = await getJson(`data/meta.json?t=${Date.now()}`);
  const v = m.generated_utc;
  if (serverVersion && v !== serverVersion) { serverVersion = v; onServer(m); }
  return 1;
}

// ---------------------------------------------------------------------------
// Quote overlays
// ---------------------------------------------------------------------------

// Live bid/ask for a server quote {venue, token|ticker, invert, bid, ask}; falls back to the server's snapshot.
export function liveQuote(q) {
  const src = q.venue === "polymarket" ? live.poly.get(q.token) : q.venue === "kalshi" ? live.kalshi.get(q.ticker) : null;
  if (!src || src.bid == null || src.ask == null) return { ...q, live: false };
  const bid = q.invert ? +(1 - src.ask).toFixed(4) : src.bid;
  const ask = q.invert ? +(1 - src.bid).toFixed(4) : src.ask;
  return { ...q, bid, ask, size: q.invert ? src.bidSize : src.askSize, live: true, t: src.t };
}
