// Premier League pages: the server's forecasts and signals with the live feed applied.
// Signal rules are ports of live/signals.py, re-run on live prices.
import { live, liveQuote, espnDate } from "./live.js";
import { clockState, remaining, inplay, cost, edge } from "./quant.js";

const LEAGUE = "eng.1";
const BIG6 = new Set(["Man United", "Liverpool", "Arsenal", "Chelsea", "Man City", "Tottenham"]);

// Live score, clock and in-play probabilities; live prices and edges. Edges are
// only shown before kick-off: the fair price is a pre-match price.
export function eplMatch(m, clock) {
  const ev = m.espn_id ? live.events.get(m.espn_id) : null;
  const v = { ...m, state: ev?.state || "pre" };
  if (ev && ev.state !== "pre") Object.assign(v, { hg: ev.hg, ag: ev.ag, clock: ev.clock, detail: ev.detail, reds: ev.reds });
  if (ev?.state === "in" && m.rates_fair && clock) {
    const cs = clockState(ev.status);
    v.rem = cs.phase === "ht" ? remaining(clock, 45, 0, true) : remaining(clock, cs.minute, cs.added);
    v.now = inplay(m.rates_fair[0], m.rates_fair[1], ev.hg, ev.ag, v.rem);
  }
  v.quotes = (m.quotes || []).map((q) => {
    const l = liveQuote(q);
    if (v.state !== "pre") return { ...l, edge: null };
    if (!l.live) return l;
    const c = cost(l.ask, l.venue, l.fee_rate);
    return { ...l, cost: c, edge: q.fair != null ? edge(q.fair, c) : null };
  });
  return v;
}

export function eplNeeds(matches, season) {
  const now = Date.now();
  const near = matches.filter((m) => { const t = new Date(m.kickoff_utc) - now; return t > -4 * 3600e3 && t < 48 * 3600e3; });
  const futures = (season?.teams || []).flatMap((t) => Object.values(t.markets || {}).flatMap((x) => Object.entries(x)));
  return {
    espn: [...near.filter((m) => m.espn_id).map((m) => ({ league: LEAGUE, date: espnDate(m.kickoff_utc) })), ...(near.length ? [{ league: LEAGUE, date: espnDate(new Date().toISOString()) }] : [])],
    poly: [...matches.flatMap((m) => m.quotes.filter((q) => q.venue === "polymarket").map((q) => q.token)), ...futures.filter(([v]) => v === "polymarket").map(([, q]) => q.token)],
    kalshi: [...matches.flatMap((m) => m.quotes.filter((q) => q.venue === "kalshi").map((q) => q.ticker)), ...futures.filter(([v]) => v === "kalshi").map(([, q]) => q.ticker)],
    kickoffs: near.map((m) => m.kickoff_utc),
  };
}

// Season-market price for a venue, live when the feed has it.
export function futuresQuote(q, venue) {
  const src = venue === "polymarket" ? live.poly.get(q.token) : live.kalshi.get(q.ticker);
  return src && src.bid != null && src.ask != null ? { ...q, bid: src.bid, ask: src.ask, live: true } : q;
}

function makerEvidence(lab, entryH, side, group) {
  const f = lab?.findings?.find((x) => x.id === "maker");
  const cells = (f?.evidence || []).filter((c) => c.side === side && c.group === group && c.improve === 0.01);
  if (!cells.length) return null;
  const c = cells.reduce((a, b) => (Math.abs(b.entry_h - entryH) < Math.abs(a.entry_h - entryH) ? b : a));
  return { finding: "maker", entry_h: c.entry_h, clv: c.clv, clv_ci90: c.clv_ci90, n: c.n_filled, fill_rate: c.fill_rate };
}

// live/signals.py maker_signals on live Kalshi prices: resting orders 24-96 h before
// kick-off, a bid for the draw or an offer on a big-six win, one cent inside the spread.
export function makerLive(matches, lab) {
  const out = [];
  for (const m of matches) {
    const hours = (new Date(m.kickoff_utc) - Date.now()) / 3600e3;
    if (m.state !== "pre" || hours < 24 || hours > 96) continue;
    for (const q of m.quotes) {
      if (q.venue !== "kalshi" || q.market !== "1x2" || q.invert || q.bid == null || q.ask == null || q.ask - q.bid < 0.01) continue;
      const team = q.selection === "H" ? m.home : q.selection === "A" ? m.away : null;
      let limit, ev, action, what;
      if (q.selection === "D") { limit = +Math.min(q.bid + 0.01, q.ask - 0.01).toFixed(2); ev = makerEvidence(lab, hours, "bid", "draw"); action = "bid"; what = "Draw"; }
      else if (BIG6.has(team)) { limit = +Math.max(q.ask - 0.01, q.bid + 0.01).toFixed(2); ev = makerEvidence(lab, hours, "ask", "big-six"); action = "offer"; what = `${team === m.home ? m.home_name : m.away_name} to win`; }
      else continue;
      if (!ev) continue;
      out.push({ type: "maker", venue: "Kalshi", match_id: m.match_id, kickoff_utc: m.kickoff_utc, home: m.home, away: m.away, home_name: m.home_name, away_name: m.away_name,
        home_code: m.home_code, away_code: m.away_code, action, what, limit, bid: q.bid, ask: q.ask, expected: ev.clv, hours_to_kickoff: hours, evidence: ev, ticker: q.ticker, live: !!q.live });
    }
  }
  return out;
}

// live/signals.py season_signals on live prices.
export function seasonLive(teams, minGap = 0.03, maxSpread = 0.06) {
  const names = { title: "win the league", top4: "finish top four", relegation: "be relegated" };
  const out = [];
  for (const t of teams) for (const mk of ["title", "top4", "relegation"]) {
    const p = t[mk];
    for (const [venue, q0] of Object.entries(t.markets?.[mk] || {})) {
      const q = futuresQuote(q0, venue);
      if (q.bid == null || q.ask == null || q.ask - q.bid > maxSpread) continue;
      const cy = cost(q.ask, venue, q.fee_rate), cn = cost(1 - q.bid, venue, q.fee_rate);
      for (const [side, prob, c] of [["yes", p, cy], ["no", 1 - p, cn]]) {
        if (prob - c > minGap && c > 0.02) out.push({ type: "season", venue: venue[0].toUpperCase() + venue.slice(1), team: t.team, team_code: t.code, market: mk, side,
          what: `${t.name} ${side === "yes" ? "to" : "not to"} ${names[mk]}`, price: side === "yes" ? q.ask : 1 - q.bid, cost: c, sim: prob, expected: prob / c - 1, live: !!q.live });
      }
    }
  }
  const best = new Map();
  out.sort((a, b) => b.expected - a.expected).forEach((s) => { const k = `${s.team}|${s.market}|${s.side}`; if (!best.has(k)) best.set(k, s); });
  return [...best.values()];
}

// live/signals.py arbitrage_signals on live prices (pre-match only).
export function arbitrageLive(matches) {
  const out = [];
  for (const m of matches) {
    if (m.state !== "pre") continue;
    const best = {};
    for (const q of m.quotes) {
      if (q.market !== "1x2" || !(q.cost > 0) || !["kalshi", "polymarket"].includes(q.venue)) continue;
      if (!best[q.selection] || q.cost < best[q.selection].cost) best[q.selection] = q;
    }
    if (Object.keys(best).length === 3) {
      const total = Object.values(best).reduce((s, q) => s + q.cost, 0);
      if (total < 0.995) out.push({ type: "arbitrage", match_id: m.match_id, kickoff_utc: m.kickoff_utc, home: m.home, away: m.away, home_code: m.home_code, away_code: m.away_code,
        total_cost: total, expected: 1 / total - 1, legs: Object.fromEntries(Object.entries(best).map(([s, q]) => [s, { venue: q.venue, price: q.ask, cost: q.cost }])) });
    }
  }
  return out;
}

