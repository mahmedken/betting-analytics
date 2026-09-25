// AFCON 2027 qualifying page and the AFCON block on Today.
//
// The server run (live/afcon.py) supplies forecasts, group odds and simulation
// inputs; between runs the page applies the live feed (live.js): ESPN scores and
// clock, in-play probabilities, DraftKings odds, Polymarket order books, and
// group odds re-simulated from live results and scores.
import { esc, pct, signed, cents, num, bar3c } from "./charts.js";
import { pairColors } from "./teams.js";
import { live, espnDate } from "./live.js";
import { clockState, remaining, inplay, shin, cost, liveGroupOdds } from "./quant.js";

const dtf = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dayf = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
const dayKey = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };
const isToday = (iso) => dayKey(iso) === dayKey(new Date().toISOString());
const LEAGUE = "caf.nations_qual";

// ESPN serves flags at 500 px; its image resizer returns a 64 px copy (under 1 kB).
export function flag(meta, size = "") {
  const logo = meta?.logo;
  if (!logo) return `<span class="badge ${size}" style="background:var(--card-2);color:var(--ink-2)">${esc(meta?.code || "")}</span>`;
  const src = `https://a.espncdn.com/combiner/i?img=${new URL(logo).pathname}&w=64&h=64`;
  return `<img class="flag ${size}" src="${esc(src)}" alt="" loading="lazy" width="30" height="30" onerror="this.style.visibility='hidden'">`;
}

// ---------------------------------------------------------------------------
// live view: server data + live feed
// ---------------------------------------------------------------------------
function liveMatch(m, clock) {
  const ev = live.events.get(m.event_id);
  const v = { ...m };
  if (ev) {
    v.state = ev.state;
    if (ev.state === "in") Object.assign(v, { live_hg: ev.hg, live_ag: ev.ag, clock: ev.clock, detail: ev.detail, reds: ev.reds });
    if (ev.state === "post") Object.assign(v, { hg: ev.hg, ag: ev.ag, reds: ev.reds });
  }
  if (v.state === "in" && v.xg && clock) {
    const cs = clockState(ev?.status || { type: { state: "in", name: String(m.status || "").toUpperCase().replace(/ /g, "_") }, displayClock: m.clock });
    v.rem = cs.phase === "ht" ? remaining(clock, 45, 0, true) : remaining(clock, cs.minute, cs.added);
    v.now = inplay(v.xg[0], v.xg[1], v.live_hg ?? 0, v.live_ag ?? 0, v.rem);
  }
  const o = live.odds.get(m.event_id);
  if (v.state === "pre" && o) v.book = { provider: o.provider, odds: o.odds, p: shin(o.odds), live: true };
  return v;
}

function table(teams, played) {
  const t = Object.fromEntries(teams.map((x) => [x, { p: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0, pts: 0 }]));
  for (const [h, a, x, y] of played) {
    for (const [me, gf, ga] of [[h, x, y], [a, y, x]]) {
      const r = t[me]; if (!r) continue;
      r.p++; r.gf += gf; r.ga += ga;
      if (gf > ga) { r.w++; r.pts += 3; } else if (gf === ga) { r.d++; r.pts++; } else r.l++;
    }
  }
  return t;
}

const simCache = new Map();
function liveGroup(g, d, sim, matches) {
  const teams = g.teams.map((t) => t.team);
  const res = (ms) => ms.filter((m) => m.group === g.group && m.state === "post" && m.hg != null).map((m) => [m.home, m.away, m.hg, m.ag]);
  const serverPlayed = res(d.matches), played = res(matches);
  const lv = {};
  matches.filter((m) => m.group === g.group && m.state === "in" && m.rem != null)
    .forEach((m) => (lv[`${m.home}|${m.away}`] = { hg: m.live_hg ?? 0, ag: m.live_ag ?? 0, rem: Math.round(m.rem * 50) / 50 }));
  const key = `${d.generated_utc}|${g.group}|${JSON.stringify(played)}|${JSON.stringify(lv)}`;
  let odds = null;
  if ((played.length !== serverPlayed.length || Object.keys(lv).length) && sim?.groups?.[g.group]) {
    if (!simCache.has(key)) {
      if (simCache.size > 60) simCache.clear();
      const server = Object.fromEntries(g.teams.map((t) => [t.team, t]));
      simCache.set(key, liveGroupOdds(server, { teams, hosts: new Set(sim.hosts), fixtures: sim.groups[g.group].fixtures, played, live: lv, rho: sim.rho, perDraw: 100, seed: 7 }, serverPlayed));
    }
    odds = simCache.get(key);
  }
  const tb = table(teams, played);
  const rows = g.teams.map((t) => {
    const q = t.market ? { ...t.market } : null;
    const lq = q?.token ? live.poly.get(q.token) : null;
    if (lq && lq.bid != null && lq.ask != null) Object.assign(q, { bid: lq.bid, ask: lq.ask, askSize: lq.askSize, bidSize: lq.bidSize, live: true });
    return { ...t, ...(odds ? odds[t.team] : {}), table: tb[t.team], market: q, moved: !!odds };
  });
  return { ...g, teams: rows, live: Object.keys(lv).length > 0, moved: !!odds };
}

function gapsOf(groups) {
  const out = [];
  for (const g of groups) for (const t of g.teams) {
    const q = t.market;
    if (!q || q.bid == null || q.ask == null || q.ask - q.bid > 0.08) continue;
    const cy = cost(q.ask, "polymarket", q.fee_rate), cn = cost(1 - q.bid, "polymarket", q.fee_rate);
    for (const [side, p, c, px, size] of [["yes", t.p_first, cy, q.ask, q.askSize], ["no", 1 - t.p_first, cn, 1 - q.bid, q.bidSize]]) {
      if (p - c > 0.04 && c > 0.02) out.push({ group: g.group, team: t.team, code: t.code, logo: t.logo, side, price: px, cost: c, model: p, size, live: !!q.live,
        what: `${t.team} ${side === "yes" ? "to win" : "not to win"} Group ${g.group}`, expected: p / c - 1 });
    }
  }
  return out.sort((a, b) => b.expected - a.expected);
}

export function afconView(d, sim, clock) {
  const matches = d.matches.map((m) => liveMatch(m, clock));
  const groups = d.groups.map((g) => liveGroup(g, d, sim, matches));
  return { matches, groups, gaps: gapsOf(groups) };
}

// What the page needs polled: scoreboards for days with matches around now, odds for the next 48 h, all group-winner books.
export function afconNeeds(d) {
  const now = Date.now();
  const near = d.matches.filter((m) => { const t = new Date(m.kickoff_utc) - now; return t > -4 * 3600e3 && t < 48 * 3600e3; });
  return {
    espn: [...near.map((m) => ({ league: LEAGUE, date: espnDate(m.kickoff_utc) })), { league: LEAGUE, date: espnDate(new Date().toISOString()) }],
    odds: near.filter((m) => m.state === "pre").map((m) => ({ league: LEAGUE, id: m.event_id })),
    poly: d.groups.flatMap((g) => g.teams.map((t) => t.market?.token)),
    kickoffs: near.map((m) => m.kickoff_utc),
  };
}

// ---------------------------------------------------------------------------
// match card
// ---------------------------------------------------------------------------
const FEW = 20;   // fewer international matches than this in eight years: rating is uncertain
const reds = (n) => (n ? `<i class="red" title="${n} red card${n > 1 ? "s" : ""}">${n > 1 ? n : ""}</i>` : "");
export function matchCard(m) {
  const H = m.home_meta || {}, A = m.away_meta || {};
  const [hc, ac] = pairColors(H.color, A.color);
  const isLive = m.state === "in";
  const mid = isLive ? `${num(m.live_hg, 0)}–${num(m.live_ag, 0)}` : "v";
  const head = isLive ? `<span class="tag live">live · ${esc(m.detail || m.clock || "")}</span>` : `<span>${esc(dtf.format(new Date(m.kickoff_utc)))}</span>`;
  const have = m.p_h != null;
  const p = isLive && m.now ? m.now : have ? [m.p_h, m.p_d, m.p_a] : null;
  const bk = m.book;
  const rg = m.range90;
  const tip = isLive && m.now
    ? `now: home ${pct(p[0], 1)} · draw ${pct(p[1], 1)} · away ${pct(p[2], 1)}\nfrom the score, the clock and the pre-match expected goals ${num(m.xg[0], 2)}–${num(m.xg[1], 2)}\n${pct(m.rem)} of a match's goals are still to come`
    : have ? `model: home ${pct(m.p_h, 1)} · draw ${pct(m.p_d, 1)} · away ${pct(m.p_a, 1)}${rg ? `\n90% range from rating uncertainty:\nhome ${pct(rg[0][0])}–${pct(rg[0][1])} · draw ${pct(rg[1][0])}–${pct(rg[1][1])} · away ${pct(rg[2][0])}–${pct(rg[2][1])}` : ""}` : "";
  const few = (m.n_matches || []).map((n, i) => [n, i ? m.away : m.home]).filter(([n]) => n < FEW);
  const bookKv = bk ? `<span data-tip="${esc(`${bk.provider} home · draw · away, margin removed (Shin method)${bk.odds ? `\nodds ${bk.odds.map((o) => num(o, 2)).join(" · ")}` : ""}${bk.live ? "\nlive from ESPN" : ""}`)}">${esc(bk.provider)}<b>${bk.p.map((x) => Math.round(100 * x)).join(" · ")}</b></span>` : `<span>no bookmaker odds yet</span>`;
  return `<div class="card am${isLive ? " is-live" : ""}">
    <div class="when">${head}<span>Group ${esc(m.group)}${m.neutral ? " · neutral venue" : ""}</span></div>
    <div class="teams"><div class="t">${flag(H)}<span>${esc(m.home)}</span>${reds(m.reds?.home)}</div><span class="sc${isLive ? "" : " v"}">${mid}</span><div class="t r">${flag(A)}<span>${esc(m.away)}</span>${reds(m.reds?.away)}</div></div>
    ${p ? `<div data-tip="${esc(tip)}">${bar3c(hc, ac, p[0], p[1], p[2])}
    <div class="pcts"><span>${pct(p[0])}<small>${isLive && m.now ? "home now" : "home"}</small></span><span>${pct(p[1])}<small>draw</small></span><span>${pct(p[2])}<small>away</small></span></div></div>
    <div class="kv">${isLive ? `<span>before kick-off<b>${[m.p_h, m.p_d, m.p_a].map((x) => Math.round(100 * x)).join(" · ")}</b></span>` : bookKv}${!isLive && m.xg ? `<span>xG<b>${num(m.xg[0], 1)}–${num(m.xg[1], 1)}</b></span>` : ""}</div>
    ${!isLive && few.length ? `<p class="small muted" style="margin:10px 0 0">${few.map(([n, t]) => `${esc(t)} has played ${n} internationals in eight years`).join("; ")}: the model knows little, see the range.</p>` : ""}`
    : `<p class="small muted" style="margin:14px 0 0">No forecast was recorded before kick-off.</p>`}
  </div>`;
}

function resultRow(m) {
  const H = m.home_meta || {}, A = m.away_meta || {};
  const f = m.p_h != null ? `${Math.round(100 * m.p_h)} · ${Math.round(100 * m.p_d)} · ${Math.round(100 * m.p_a)}` : "";
  return `<div class="res"><span class="g">${esc(m.group)}</span><span class="h"><span>${esc(m.home)}</span>${flag(H, "sm")}</span><span class="s">${num(m.hg, 0)}–${num(m.ag, 0)}</span><span class="a">${flag(A, "sm")}<span>${esc(m.away)}</span></span>${f ? `<span class="f" data-tip="model forecast before kick-off: home · draw · away">${f}</span>` : ""}</div>`;
}

// ---------------------------------------------------------------------------
// group card
// ---------------------------------------------------------------------------
function groupCard(g, gapKeys, liveMatches) {
  const rows = g.teams.slice().sort((a, b) => b.table.pts - a.table.pts || (b.table.gf - b.table.ga) - (a.table.gf - a.table.ga) || b.table.gf - a.table.gf || b.p_qualify - a.p_qualify);
  const line = (t) => {
    const q = t.market;
    const midp = q && q.bid != null && q.ask != null ? (q.bid + q.ask) / 2 : q?.ask;
    const gap = gapKeys.has(t.team);
    const tip = q ? `${t.team} to win Group ${g.group} on Polymarket${q.live ? " (live order book)" : ""}\nbid ${cents(q.bid, 1)} · ask ${cents(q.ask, 1)}${q.askSize != null ? `\n${Math.round(q.askSize)} contracts offered at the ask` : ""}\n$${Math.round(q.volume || 0).toLocaleString()} traded` : "";
    const pos = t.positions.map((p, i) => `${i + 1}${["st", "nd", "rd", "th"][i]} ${pct(p)}`).join(" · ");
    return `<div class="gr">
      <span class="tm" data-tip="${esc(`${t.team}\n${t.table.w}W ${t.table.d}D ${t.table.l}L, goals ${t.table.gf}–${t.table.ga}\nfinish: ${pos}\nexpected points ${num(t.exp_points, 1)}`)}">${flag(t, "sm")}<span>${esc(t.team)}</span></span>
      <span class="n">${t.table.p}</span><span class="n b">${t.table.pts}</span>
      <span class="q">${t.host ? `<span class="host">host</span>` : `<span class="t"><i style="width:${100 * t.p_qualify}%"></i></span><span class="n">${pct(t.p_qualify)}</span>`}</span>
      <span class="n">${pct(t.p_first)}</span>
      <span class="n${gap ? " hl" : " muted"}"${tip ? ` data-tip="${esc(tip)}"` : ""}>${midp != null ? cents(midp) : "–"}</span>
    </div>`;
  };
  const lm = liveMatches.filter((m) => m.group === g.group);
  return `<div class="card grp${g.live ? " is-live" : ""}">
    <div class="between" style="margin-bottom:8px"><h3 style="margin:0">Group ${esc(g.group)}</h3>${g.hosts.length ? `<span class="tag">${esc(g.hosts[0])} host: one other place</span>` : ""}</div>
    ${lm.map((m) => `<p class="small" style="margin:0 0 8px"><span class="tag live">live</span> ${esc(m.home)} ${num(m.live_hg, 0)}–${num(m.live_ag, 0)} ${esc(m.away)} · ${esc(m.clock || "")}</p>`).join("")}
    <div class="gr hd"><span></span><span class="n">P</span><span class="n">Pts</span><span>qualify</span><span class="n">1st</span><span class="n">mkt</span></div>
    ${rows.map(line).join("")}
    ${g.moved ? `<p class="small muted" style="margin:8px 0 0">Odds updated for ${g.live ? "the live score" : "results since the last model run"}.</p>` : ""}
  </div>`;
}

// ---------------------------------------------------------------------------
// gap card (Polymarket group winner)
// ---------------------------------------------------------------------------
function gapCard(x) {
  return `<div class="card sig">
    <div class="between"><span class="tag">Polymarket · group winner${x.live ? " · live" : ""}</span><span class="tag">unproven</span></div>
    <div class="row">${flag(x)}<div class="what" style="font-size:22px">${esc(x.what)}</div></div>
    <div class="prices">
      <div><div class="l">${x.side === "yes" ? "buy Yes at" : "buy No at"}</div><div class="v">${cents(x.price)}</div></div>
      <div><div class="l">model</div><div class="v">${pct(x.model)}</div></div>
      <div class="e"><div class="l">gap after fee</div><div class="v edge">+${Math.round(100 * (x.model - x.cost))} pts</div></div>
    </div>
    ${x.size != null ? `<div class="why">${Math.round(x.size).toLocaleString()} contracts available at this price.</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------
export async function pageAfcon(app, load) {
  const opt = (n) => load(n).catch(() => null);
  const [d, bt, sim, ip] = await Promise.all([load("afcon.json"), opt("afcon_backtest.json"), opt("afcon_sim.json"), opt("inplay.json")]);
  const v = afconView(d, sim, ip?.clock);
  const ms = v.matches;
  const liveNow = ms.filter((m) => m.state === "in");
  const now = Date.now();
  const soon = ms.filter((m) => m.state === "pre" && new Date(m.kickoff_utc) - now < 48 * 3600e3);
  const done = ms.filter((m) => m.state === "post").reverse();
  const later = ms.filter((m) => m.state === "pre" && new Date(m.kickoff_utc) - now >= 48 * 3600e3);
  const byDay = (list) => {
    const out = new Map();
    list.forEach((m) => { const k = dayKey(m.kickoff_utc); if (!out.has(k)) out.set(k, []); out.get(k).push(m); });
    return [...out.values()].map((xs) => `<h3 class="day">${isToday(xs[0].kickoff_utc) ? "Today" : esc(dayf.format(new Date(xs[0].kickoff_utc)))}</h3><div class="grid">${xs.map(matchCard).join("")}</div>`).join("");
  };
  const gaps = v.gaps;
  const gapKeys = new Set(gaps.map((x) => x.team));
  const b = bt?.bookmaker, vb = b?.value_bets;
  const rec = d.record;
  const val = ip?.validation;
  const first = ms.length ? new Date(ms[0].kickoff_utc) : null, last = ms.length ? new Date(ms[ms.length - 1].kickoff_utc) : null;
  const df = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
  const ht = val?.checkpoints?.find((c) => c.label === "half time");
  app.innerHTML = `
    <div class="hero"><h1>${liveNow.length ? `<em>${liveNow.length} live</em> ` : ""}AFCON qualifying</h1>
      <div class="chips">${first ? `<span class="chip">this window <b>${df.format(first)} – ${df.format(last)}</b></span>` : ""}<span class="chip"><b>${done.length}</b> of ${ms.length} played</span><span class="chip"><b>${gaps.length}</b> price gaps</span></div></div>

    ${liveNow.length ? `<h2>Live</h2><div class="grid">${liveNow.map(matchCard).join("")}</div>
    ${val ? `<p class="small muted">Live probabilities come from the score, the clock and each side's pre-match expected goals. Tested on ${val.n_matches} past qualifiers: when they gave the leading team ${pct(val.leader.mean_pred)}, it won ${pct(val.leader.freq)} of the time.</p>` : ""}` : ""}

    ${gaps.length ? `<h2>Group-winner prices the model disagrees with</h2>
    <div class="grid">${gaps.slice(0, 3).map(gapCard).join("")}</div>
    <p class="small muted">Polymarket's group-winner markets have traded a few hundred to a few thousand dollars each, so a handful of traders set these prices. The model's group odds come from 10,000 simulations of the remaining fixtures and follow live scores. They have not yet been tested against these prices, so treat the gaps as leads, not signals.${gaps.length > 3 ? ` <a href="#afcon-gaps">All ${gaps.length} ↓</a>` : ""}</p>` : ""}

    <h2>Next 48 hours</h2>
    ${byDay(soon) || `<div class="notice"><div class="dot">–</div><p>No matches in the next two days.</p></div>`}
    ${later.length ? `<details class="more"><summary>Later this window · ${later.length} matches</summary>${byDay(later)}</details>` : ""}

    ${done.length ? `<h2>Results</h2><div class="card results">${done.map(resultRow).join("")}</div>` : ""}

    <h2>Groups <span class="small muted" style="font:500 13px var(--sans);text-transform:none;letter-spacing:0">qualify and 1st: model · mkt: Polymarket, highlighted where it disagrees</span></h2>
    <div class="grid groups">${v.groups.map((g) => groupCard(g, gapKeys, liveNow)).join("")}</div>

    ${bt ? `<h2>Is the model any good?</h2>
    <div class="grid">
      <div class="card find"><span class="tag hot">beats Elo</span><div class="big">${num(bt.model.rps, 3)}</div><h3>vs ${num(bt.elo.rps, 3)} for Elo</h3><p>Ranked probability score (lower is better) on <b>${bt.n_test}</b> qualifiers since 2022, each forecast using only earlier matches. p = ${num(bt.model_vs_elo.p_value, 2)}.</p></div>
      ${b ? `<div class="card find"><span class="tag">level with the bookmaker</span><div class="big">${num(b.rps_model, 3)}</div><h3>vs ${num(b.rps_book, 3)} for ${esc(b.provider)}</h3><p>On the <b>${b.n}</b> qualifiers of October–November 2024 with closing odds. The difference is noise (p = ${num(b.model_vs_book.p_value, 2)}). The book's margin was ${pct(b.margin, 1)}.</p></div>` : ""}
      ${vb ? `<div class="card find"><span class="tag">not proven</span><div class="big">${signed(vb.roi, 0)}</div><h3>Betting the model's value</h3><p>Return on <b>${vb.n}</b> bets where the model saw 5%+ value at ${esc(b.provider)}. The 90% interval, ${signed(vb.roi_ci90[0], 0)} to ${signed(vb.roi_ci90[1], 0)}, includes losing money.</p></div>` : ""}
      ${val ? `<div class="card find"><span class="tag hot">calibrated</span><div class="big">${num(100 * val.ece, 1)}<small> pts</small></div><h3>Live probabilities</h3><p>Average gap between forecast and outcome frequency at six points of <b>${val.n_matches}</b> past qualifiers. Ranked probability score falls from ${num(val.checkpoints[0].rps, 3)} at kick-off to ${num(ht?.rps, 3)} at half time.</p></div>` : ""}
    </div>
    <p class="small muted">Live record: ${rec.n_settled ? `${rec.n_settled} forecasts settled, ranked probability score ${num(rec.rps_model, 3)}${rec.n_book ? ` (bookmaker ${num(rec.rps_book, 3)} on the ${rec.n_book} with odds)` : ""}.` : "no forecasts settled yet."} Every forecast is committed to the repository before kick-off.</p>` : ""}

    ${gaps.length > 3 ? `<h2 id="afcon-gaps">Every price gap</h2>
    <div class="tbl"><table><thead><tr><th>contract</th><th class="n">price</th><th class="n">cost with fee</th><th class="n">model</th><th class="n">gap</th></tr></thead><tbody>
      ${gaps.map((x) => `<tr><td><span class="team">${flag(x, "sm")}${esc(x.what)}</span></td><td class="n">${cents(x.price, 1)}</td><td class="n muted">${cents(x.cost, 1)}</td><td class="n">${pct(x.model)}</td><td class="n"><span class="hl">+${Math.round(100 * (x.model - x.cost))} pts</span></td></tr>`).join("")}
    </tbody></table></div>` : ""}

    <details class="more" style="margin-top:28px"><summary>How the forecasts are made</summary><div class="prose" style="margin-top:6px">
      <p><b>Rule.</b> ${esc(d.rule)} Ties on points are broken by head-to-head points, goal difference, goals and away goals, then overall goal difference and goals.</p>
      <p><b>Model.</b> A Dixon–Coles scoreline model fitted to every men's international of the last eight years, with home advantage only at home venues, older matches down-weighted (half-life ${num(Math.log(2) / d.config.xi / 365, 1)} years) and each team's attack and defence shrunk toward average. Settings were chosen on 2018–21 qualifiers and frozen before testing on 2022 onward.</p>
      <p><b>Groups.</b> Remaining fixtures are simulated ${d.n_sims.toLocaleString()} times. Each simulation draws the team ratings from their estimation uncertainty, so a team with few recent matches gets a wider range. A team that has played its latest home qualifier at a neutral venue is assumed to keep doing so.</p>
      <p><b>Live.</b> Scores, the match clock and DraftKings odds come from ESPN and Polymarket prices from its order book, polled by this page every 15–120 seconds. During a match, goals still to come are drawn from Poisson distributions with the pre-match expected goals scaled by the share of goals international matches produce after the current minute${ip ? ` (${ip.clock.n_goals.toLocaleString()} goals in ${ip.clock.n_matches.toLocaleString()} internationals since 2010; stoppage time averages ${num(ip.clock.len1, 1)} and ${num(ip.clock.len2, 1)} minutes)` : ""}. Red cards and the extra risks trailing teams take are not modelled. When a result or live score arrives, the page re-simulates that group with the same parameter draws as the server and moves the server's odds by the difference.</p>
      <p><b>What it does not know.</b> Squads, injuries, coaching changes and travel. It learns only from results.</p>
      <p class="small muted">Sources: international results and goal times compiled by Mart Jürisoo (github.com/martj42/international_results); fixtures, live scores, standings and DraftKings odds from ESPN; Polymarket prices.</p>
    </div></details>`;
  return afconNeeds(d);
}

// ---------------------------------------------------------------------------
// block on the Today page
// ---------------------------------------------------------------------------
export function afconToday(d, clock) {
  if (!d) return "";
  const now = Date.now();
  const ms = d.matches.map((m) => liveMatch(m, clock)).filter((m) => m.state === "in" || (m.state === "pre" && new Date(m.kickoff_utc) - now < 24 * 3600e3));
  if (!ms.length) return "";
  const n = ms.filter((m) => m.state === "in").length;
  return `<h2>AFCON qualifying${n ? ` <span class="tag live">${n} live</span>` : ""} <a href="#/afcon">Groups and all matches →</a></h2>
    <div class="grid">${ms.slice(0, 6).map(matchCard).join("")}</div>`;
}
