// Numerical code for the live layer, ported from the Python so the browser
// computes the same numbers as the server:
//   remaining(), inplay()      models/inplay.py
//   shin()                     models/devig.py
//   dcMatrix()                 models/dixon_coles.py score_matrix
//   cost(), edge()             live/pricing.py cost_per_contract
//   rankGroup(), simulateGroup live/afcon.py _rank_group / simulate_groups
// Pure functions only; tested in site/tests/quant.test.mjs.

const LOGFACT = [0];
for (let k = 1; k <= 40; k++) LOGFACT[k] = LOGFACT[k - 1] + Math.log(k);
export const poisson = (k, mu) => (mu <= 0 ? (k === 0 ? 1 : 0) : Math.exp(k * Math.log(mu) - mu - LOGFACT[k]));

// ---------------------------------------------------------------------------
// match clock
// ---------------------------------------------------------------------------

// ESPN status -> {phase, minute, added, halftime}. phase: pre | live | ht | post.
// "17'" is shown during the 17th minute, so 16.5 minutes have elapsed on average.
export function clockState(status) {
  const name = status?.type?.name || "";
  const state = status?.type?.state;
  if (state === "pre") return { phase: "pre" };
  if (state === "post" || /FULL_TIME|FINAL|END_OF_REGULATION/.test(name)) return { phase: "post" };
  if (/HALFTIME/.test(name)) return { phase: "ht", minute: 45, added: 0, halftime: true };
  const parts = String(status?.displayClock || "0").replace(/'/g, "").split("+").map((x) => parseFloat(x) || 0);
  const base = parts[0], add = parts[1] || 0;
  if (add > 0) return { phase: "live", minute: base, added: add - 0.5, halftime: false };
  return { phase: "live", minute: Math.max(0, base - 0.5), added: 0, halftime: false };
}

// Share of the match's goals still to come (GoalClock.remaining in Python).
export function remaining(clock, minute, added = 0, halftime = false) {
  const { share, stop1, stop2, len1, len2 } = clock;
  const sum = (a, b) => { let s = 0; for (let i = a; i < b; i++) s += share[i]; return s; };
  const after = (e) => {
    e = Math.min(Math.max(e, 0), 90);
    const k = Math.floor(e);
    return sum(k + 1, 90) + (k < 90 ? share[k] * (1 - (e - k)) : 0);
  };
  const second = sum(45, 90) + stop2;
  if (halftime) return second;
  if (minute >= 90) return added <= 0 ? stop2 : stop2 * Math.max(0, 1 - added / len2);
  if (minute >= 45 && added > 0) return stop1 * Math.max(0, 1 - added / len1) + second;
  if (minute < 45) return after(minute) - sum(45, 90) + stop1 + second;
  return after(minute) + stop2;
}

// P(home win, draw, away win) at full time from the score now and the share of goals to come.
export function inplay(lam, nu, hg, ag, rem, gmax = 15) {
  const ph = [], pa = [];
  for (let k = 0; k < gmax; k++) { ph.push(rem > 0 ? poisson(k, lam * rem) : +(k === 0)); pa.push(rem > 0 ? poisson(k, nu * rem) : +(k === 0)); }
  let h = 0, d = 0, a = 0;
  for (let i = 0; i < gmax; i++) for (let j = 0; j < gmax; j++) {
    const p = ph[i] * pa[j], diff = hg + i - (ag + j);
    if (diff > 0) h += p; else if (diff === 0) d += p; else a += p;
  }
  const s = h + d + a;
  return [h / s, d / s, a / s];
}

// ---------------------------------------------------------------------------
// prices
// ---------------------------------------------------------------------------

export const americanToDecimal = (ml) => { const x = +ml; return !isFinite(x) || x === 0 ? NaN : 1 + (x > 0 ? x / 100 : 100 / -x); };

// Shin (1993) margin removal for one market given decimal odds.
export function shin(odds) {
  const pi = odds.map((o) => 1 / o);
  if (pi.some((x) => !isFinite(x))) return pi.map(() => NaN);
  const book = pi.reduce((a, b) => a + b, 0);
  const probs = (z) => pi.map((p) => (Math.sqrt(z * z + (4 * (1 - z) * p * p) / book) - z) / (2 * (1 - z)));
  const f = (z) => probs(z).reduce((a, b) => a + b, 0) - 1;
  let lo = 0, hi = 0.4, flo = f(lo);
  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi), fm = f(mid);
    if (Math.sign(fm) === Math.sign(flo)) { lo = mid; flo = fm; } else hi = mid;
  }
  const p = probs(0.5 * (lo + hi)), s = p.reduce((a, b) => a + b, 0);
  return p.map((x) => x / s);
}

// All-in cost of one $1 contract bought at `ask` as a taker.
export function cost(ask, venue, feeRate) {
  if (ask == null || !isFinite(ask)) return NaN;
  if (venue === "kalshi") return ask + 0.07 * ask * (1 - ask);
  if (venue === "polymarket") { const r = feeRate == null || !isFinite(feeRate) ? 0.05 : feeRate; return ask + r * ask * (1 - ask); }
  return ask;
}
export const edge = (p, c) => (c > 0 && c < 1 ? p / c - 1 : NaN);

// Best bid / ask from a Polymarket CLOB book ({bids:[{price,size}], asks:[...]}).
export function bestOfBook(b) {
  let bid = null, ask = null, bidSize = 0, askSize = 0;
  for (const x of b.bids || []) { const p = +x.price; if (bid === null || p > bid) { bid = p; bidSize = +x.size; } }
  for (const x of b.asks || []) { const p = +x.price; if (ask === null || p < ask) { ask = p; askSize = +x.size; } }
  return { bid, ask, bidSize, askSize };
}

// ---------------------------------------------------------------------------
// Dixon-Coles scoreline matrix and sampling
// ---------------------------------------------------------------------------
export function dcMatrix(lam, nu, rho, G = 10) {
  lam = Math.min(Math.max(lam, 0.02), 12); nu = Math.min(Math.max(nu, 0.02), 12);
  const m = new Float64Array((G + 1) * (G + 1));
  let s = 0;
  for (let x = 0; x <= G; x++) for (let y = 0; y <= G; y++) {
    let v = poisson(x, lam) * poisson(y, nu);
    if (x === 0 && y === 0) v *= 1 - lam * nu * rho;
    else if (x === 0 && y === 1) v *= 1 + lam * rho;
    else if (x === 1 && y === 0) v *= 1 + nu * rho;
    else if (x === 1 && y === 1) v *= 1 - rho;
    v = Math.max(v, 0); m[x * (G + 1) + y] = v; s += v;
  }
  const cdf = new Float64Array(m.length);
  let c = 0;
  for (let i = 0; i < m.length; i++) { c += m[i] / s; cdf[i] = c; }
  return { G, cdf };
}
function sampleCdf(cdf, u) {
  let lo = 0, hi = cdf.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (cdf[mid] < u) lo = mid + 1; else hi = mid; }
  return lo;
}
// Poisson draw by inverting the CDF with one uniform (keeps random streams aligned across runs).
function poissonInv(mu, u) {
  if (mu <= 0) return 0;
  let k = 0, p = Math.exp(-mu), c = p;
  while (u > c && k < 40) { k++; p *= mu / k; c += p; }
  return k;
}

// Small seeded generator so a page shows the same simulation on every render.
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// ---------------------------------------------------------------------------
// AFCON groups
// ---------------------------------------------------------------------------

// Order teams 1st..4th: points; among teams level on points, head-to-head points,
// goal difference, goals and away goals; then overall goal difference, goals,
// away goals; then lots. results: [home, away, hg, ag].
export function rankGroup(teams, results, rand) {
  const tally = (set) => {
    const st = {};
    teams.forEach((t) => (st[t] = [0, 0, 0, 0]));
    for (const [h, a, x, y] of results) {
      if (set && !(set.has(h) && set.has(a))) continue;
      st[h][1] += x - y; st[h][2] += x;
      st[a][1] += y - x; st[a][2] += y; st[a][3] += y;
      if (x > y) st[h][0] += 3; else if (x < y) st[a][0] += 3; else { st[h][0] += 1; st[a][0] += 1; }
    }
    return st;
  };
  const st = tally(null);
  const byPts = new Map();
  teams.forEach((t) => { const p = st[t][0]; if (!byPts.has(p)) byPts.set(p, []); byPts.get(p).push(t); });
  const order = [];
  for (const pts of [...byPts.keys()].sort((a, b) => b - a)) {
    const tied = byPts.get(pts);
    if (tied.length === 1) { order.push(tied[0]); continue; }
    const h2h = tally(new Set(tied));
    const lot = Object.fromEntries(tied.map((t) => [t, rand()]));
    const key = (t) => [h2h[t][0], h2h[t][1], h2h[t][2], h2h[t][3], st[t][1], st[t][2], st[t][3], lot[t]];
    tied.sort((a, b) => { const ka = key(a), kb = key(b); for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return kb[i] - ka[i]; return 0; });
    order.push(...tied);
  }
  return order;
}

// Group odds from the server's parameter draws.
//   fixtures  [{home, away, lam:[draws], nu:[draws]}]  unplayed at the last server run
//   played    [[home, away, hg, ag]]                    finished results (server + live feed)
//   live      {"home|away": {hg, ag, rem}}              matches in progress
// Fixtures in `played` keep their result; fixtures in `live` add goals to the current
// score from Poisson(lam * rem), Poisson(nu * rem); the rest are drawn from the
// Dixon-Coles scoreline distribution. Every fixture uses exactly two uniforms per
// simulation and lots come from a separate stream, so two calls with the same seed
// share their random numbers (common random numbers; see liveGroupOdds).
export function simulateGroup({ teams, hosts, fixtures, played, live, rho, perDraw = 100, seed = 1 }) {
  const rand = mulberry32(seed), lots = mulberry32(seed ^ 0x9e3779b9);
  const result = new Map(played.map(([h, a, x, y]) => [`${h}|${a}`, [x, y]]));
  const fixed = played.filter(([h, a]) => !fixtures.some((f) => f.home === h && f.away === a));
  const draws = fixtures.length ? fixtures[0].lam.length : 1;
  const hostIn = teams.filter((t) => hosts.has(t));
  const pos = Object.fromEntries(teams.map((t) => [t, [0, 0, 0, 0]]));
  const qual = Object.fromEntries(teams.map((t) => [t, 0]));
  const pts = Object.fromEntries(teams.map((t) => [t, 0]));
  let n = 0;
  for (let d = 0; d < draws; d++) {
    const mats = fixtures.map((f) => dcMatrix(f.lam[d], f.nu[d], rho));
    for (let s = 0; s < perDraw; s++) {
      const res = fixed.slice();
      fixtures.forEach((f, j) => {
        const u1 = rand(), u2 = rand(), key = `${f.home}|${f.away}`;
        const done = result.get(key), lv = live[key];
        if (done) res.push([f.home, f.away, done[0], done[1]]);
        else if (lv) res.push([f.home, f.away, lv.hg + poissonInv(f.lam[d] * lv.rem, u1), lv.ag + poissonInv(f.nu[d] * lv.rem, u2)]);
        else { const k = sampleCdf(mats[j].cdf, u1), G1 = mats[j].G + 1; res.push([f.home, f.away, Math.floor(k / G1), k % G1]); }
      });
      const order = rankGroup(teams, res, lots);
      order.forEach((t, i) => pos[t][i]++);
      if (hostIn.length) { qual[hostIn[0]]++; qual[order.find((t) => !hosts.has(t))]++; }
      else { qual[order[0]]++; qual[order[1]]++; }
      for (const [h, a, x, y] of res) { pts[h] += x > y ? 3 : x === y ? 1 : 0; pts[a] += y > x ? 3 : x === y ? 1 : 0; }
      n++;
    }
  }
  return Object.fromEntries(teams.map((t) => [t, {
    p_first: pos[t][0] / n, positions: pos[t].map((v) => v / n), p_qualify: qual[t] / n, exp_points: pts[t] / n,
  }]));
}

// Server odds moved by the change the live feed implies. Simulating the server's
// state and the live state with the same random numbers and adding the difference
// to the server's 10,000-draw figures keeps Monte Carlo noise out of the change,
// and leaves the server's numbers untouched when nothing has happened.
export function liveGroupOdds(server, args, serverPlayed) {
  const base = simulateGroup({ ...args, played: serverPlayed, live: {} });
  const cur = simulateGroup(args);
  const out = {};
  for (const t of args.teams) {
    const s = server[t], b = base[t], c = cur[t];
    const clip = (x) => Math.min(1, Math.max(0, x));
    out[t] = {
      p_first: clip(s.p_first + (c.p_first - b.p_first)), p_qualify: clip(s.p_qualify + (c.p_qualify - b.p_qualify)),
      positions: s.positions.map((v, i) => clip(v + (c.positions[i] - b.positions[i]))), exp_points: s.exp_points + (c.exp_points - b.exp_points),
    };
  }
  return out;
}
