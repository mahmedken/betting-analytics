import { esc, pct, signed, cents, num, badge, bar3, barsCI, teamBars, pairBars, dotRow, heatmap, posStrip, lines, legend, placeTip } from "./charts.js";
import { pageAfcon, afconToday, afconNeeds } from "./afcon.js";
import { live, start, need } from "./live.js";
import { eplMatch, eplNeeds, futuresQuote, makerLive, seasonLive, arbitrageLive } from "./eplive.js";

const app = document.getElementById("app");
const tip = document.getElementById("tip");
const cache = new Map();
let version = "";

const load = (name) => {
  if (!cache.has(name)) {
    cache.set(name, fetch(`data/${name}${version ? `?v=${encodeURIComponent(version)}` : ""}`, { cache: name === "meta.json" ? "no-store" : "default" })
      .then((r) => { if (!r.ok) throw new Error(`${name} ${r.status}`); return r.json(); }));
  }
  return cache.get(name);
};
const opt = (name) => load(name).catch(() => null);

const dtf = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dfm = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" });
const kick = (iso) => dtf.format(new Date(iso));
const day = (d) => dfm.format(d);
const ago = (iso) => { const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const VENUE = { kalshi: "Kalshi", polymarket: "Polymarket", betfair: "Betfair", bet365: "Bet365", "best price": "Best price", average: "Book avg" };
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const VERDICT = { "edge": "hot", "edge, hard to use": "hot", "suggestive": "", "real, below fees": "", "no edge": "cold", "unproven": "" };

function teamsRow(m, size = "sm", score = null) {
  return `<div class="teams"><div class="t">${badge(m.home, m.home_code, size)}<span>${esc(m.home_name || m.home)}</span></div>${score ? `<span class="sc">${esc(score)}</span>` : `<span class="vs">v</span>`}<div class="t r">${badge(m.away, m.away_code, size)}<span>${esc(m.away_name || m.away)}</span></div></div>`;
}
const fair = (m, market, sel, line = null) => (m.markets.find((x) => x.market === market && x.selection === sel && (x.line ?? null) === line) || {});

// ---------------------------------------------------------------------------
// signal cards
// ---------------------------------------------------------------------------
function makerCard(s) {
  const draw = s.what === "Draw";
  const verb = s.action === "bid" ? "Bid the draw" : `Offer ${esc(s.what.replace(" to win", ""))} to win`;
  const how = s.action === "bid" ? `limit buy Yes at ${cents(s.limit)}` : `limit sell Yes at ${cents(s.limit)} (= buy No at ${cents(1 - s.limit)})`;
  const e = s.evidence;
  return `<div class="card sig">
    <div class="between"><span class="tag hot">Kalshi · limit order</span><span class="small muted">${esc(kick(s.kickoff_utc))}</span></div>
    ${teamsRow(s)}
    <div class="what">${verb}</div>
    <div class="prices">
      <div><div class="l">post at</div><div class="v">${cents(s.limit)}</div></div>
      <div><div class="l">bid / ask now</div><div class="v">${Math.round(100 * s.bid)} / ${Math.round(100 * s.ask)}</div></div>
      <div class="e"><div class="l">backtest edge</div><div class="v edge">${signed(e.clv)}</div></div>
    </div>
    <div class="why">${draw ? "Retail money underprices draws a few days out." : "Retail money overpays for big clubs a few days out."}
      <b>${e.n} filled orders</b> placed ${e.entry_h} h before kick-off beat the sharp close by ${signed(e.clv_ci90[0])} to ${signed(e.clv_ci90[1])}. Order: ${how}.</div>
  </div>`;
}

function seasonCard(s) {
  return `<div class="card sig">
    <div class="between"><span class="tag">${esc(s.venue)} · season market</span><span class="tag">suggestive</span></div>
    <div class="row">${badge(s.team, s.team_code)}<div class="what" style="font-size:22px">${esc(s.what)}</div></div>
    <div class="prices">
      <div><div class="l">price</div><div class="v">${cents(s.price)}</div></div>
      <div><div class="l">market view</div><div class="v">${pct(s.sim)}</div></div>
      <div class="e"><div class="l">gap</div><div class="v edge">+${Math.round(100 * (s.sim - s.price))} pts</div></div>
    </div>
    <div class="why">Season prices lag the team strengths priced into match odds. Last season this view beat Kalshi's season prices on Brier score, not yet significantly.</div>
  </div>`;
}

function bestCard(s) {
  const sel = s.market === "1x2" ? (s.selection === "D" ? "Draw" : `${s.selection === "H" ? s.home : s.away} win`) : `${s.selection} ${s.line} goals`;
  return `<div class="card sig">
    <div class="between"><span class="tag hot">Bookmaker · best price</span><span class="small muted">${esc(kick(s.kickoff_utc))}</span></div>
    ${teamsRow(s)}
    <div class="what">${esc(sel)}</div>
    <div class="prices">
      <div><div class="l">best odds</div><div class="v">${num(s.odds, 2)}</div></div>
      <div><div class="l">fair odds</div><div class="v">${num(1 / s.fair, 2)}</div></div>
      <div class="e"><div class="l">edge</div><div class="v edge">${signed(s.expected)}</div></div>
    </div>
    <div class="why">The longest bookmaker price beats the fair price. Bets like this beat the sharp close by ${signed(s.evidence?.clv)} on average since 2019.</div>
  </div>`;
}

function arbCard(s) {
  return `<div class="card sig">
    <div class="between"><span class="tag hot">Cross-venue arbitrage</span><span class="small muted">${esc(kick(s.kickoff_utc))}</span></div>
    ${teamsRow(s)}
    <div class="what">All three outcomes for ${cents(s.total_cost, 1)}</div>
    <div class="why">${Object.entries(s.legs).map(([k, l]) => `${k === "H" ? "home" : k === "D" ? "draw" : "away"} on ${VENUE[l.venue]} at ${cents(l.price)}`).join(" · ")}. Pays $1 whatever happens: ${signed(s.expected)} locked in.</div>
  </div>`;
}

// ---------------------------------------------------------------------------
// fixture card
// ---------------------------------------------------------------------------
function fixtureCard(m) {
  const H = fair(m, "1x2", "H").fair, D = fair(m, "1x2", "D").fair, A = fair(m, "1x2", "A").fair;
  const k = (sel) => m.quotes.find((q) => q.venue === "kalshi" && q.market === "1x2" && q.selection === sel);
  const kh = k("H"), kd = k("D"), ka = k("A");
  const kal = kh && kd && ka ? `${Math.round(50 * (kh.bid + kh.ask))}·${Math.round(50 * (kd.bid + kd.ask))}·${Math.round(50 * (ka.bid + ka.ask))}` : "not open";
  const isLive = m.state === "in", done = m.state === "post";
  const p = isLive && m.now ? m.now : [H, D, A];
  const sig = isLive ? `<span class="tag live">live · ${esc(m.detail || m.clock || "")}</span>` : done ? `<span class="tag">full time</span>`
    : (m.signals || []).length ? `<span class="tag hot">${m.signals.length} signal${m.signals.length > 1 ? "s" : ""}</span>` : `<span>${m.xg_market ? "model + market" : "model"}</span>`;
  return `<a class="card${isLive ? " is-live" : ""}" href="#/match/${esc(m.match_id)}">
    <div class="when"><span>${esc(kick(m.kickoff_utc))}</span>${sig}</div>
    ${teamsRow(m, "sm", isLive || done ? `${m.hg}–${m.ag}` : null)}
    ${done ? "" : `<div data-tip="${esc(isLive && m.now ? `now, from the score and the clock: home ${pct(p[0], 1)} · draw ${pct(p[1], 1)} · away ${pct(p[2], 1)}\nbefore kick-off: ${pct(H)} · ${pct(D)} · ${pct(A)}` : "")}">${bar3(m.home, m.away, p[0], p[1], p[2])}
    <div class="pcts"><span>${Math.round(100 * p[0])}<small>${isLive ? "home now" : "home"}</small></span><span>${Math.round(100 * p[1])}<small>draw</small></span><span>${Math.round(100 * p[2])}<small>away</small></span></div></div>`}
    <div class="kv">${isLive || done ? `<span>before kick-off<b>${Math.round(100 * H)}·${Math.round(100 * D)}·${Math.round(100 * A)}</b></span>` : `<span>xG<b>${num(m.xg_model[0], 1)}–${num(m.xg_model[1], 1)}</b></span><span>over 2.5<b>${pct(fair(m, "total", "over", 2.5).fair)}</b></span><span>Kalshi<b>${kal}</b></span>`}</div>
  </a>`;
}

function bigNumber(f) {
  if (f.number == null) return "—";
  if (f.number_kind === "pct") return `${f.number > 0 ? "+" : ""}${(100 * f.number).toFixed(1)}<small>%</small>`;
  if (f.number_kind === "cents") return `+${(100 * f.number).toFixed(1)}<small>¢</small>`;
  if (f.number_kind === "count") return `${f.number}<small>/${f.number_of}</small>`;
  if (f.number_kind === "rps_diff") return `${f.number >= 0 ? "+" : "−"}${Math.abs(f.number).toFixed(4)}`;
  return String(f.number);
}
function findingCard(f) {
  return `<a class="card find" href="#/lab">
    <span class="tag ${VERDICT[f.verdict] ?? ""}">${esc(f.verdict)}</span>
    <div class="big">${bigNumber(f)}</div>
    <h3>${esc(f.title)}</h3>
    <p>${esc(f.number_label)}</p></a>`;
}

// ---------------------------------------------------------------------------
// Today
// ---------------------------------------------------------------------------
async function pageToday() {
  const [meta, sig, md, lab, af, ip, ss] = await Promise.all([load("meta.json"), load("signals.json"), load("matches.json"), opt("lab.json"), opt("afcon.json"), opt("inplay.json"), opt("season.json")]);
  const clock = ip?.clock;
  const matches = md.matches.map((m) => eplMatch(m, clock));
  const started = new Set(matches.filter((m) => m.state !== "pre").map((m) => m.match_id));
  const maker = lab ? makerLive(matches, lab) : sig.maker;
  const best = sig.best_price.filter((s) => !started.has(s.match_id));
  const arb = arbitrageLive(matches);
  const seasonAll = ss ? seasonLive(ss.teams) : sig.season;
  const live = [...maker, ...best, ...arb];
  const season = seasonAll.slice(0, 3);
  const gw = matches.length ? matches[0].gameweek : "";
  const first = matches.length ? new Date(matches[0].kickoff_utc) : null;
  const opens = first ? new Date(first.getTime() - 96 * 3600e3) : null;
  const headline = live.length ? `<em>${live.length}</em> live signal${live.length > 1 ? "s" : ""}` : `Gameweek ${gw}`;
  const liveHtml = live.length
    ? `<div class="grid two">${maker.map(makerCard).join("")}${best.map(bestCard).join("")}${arb.map(arbCard).join("")}</div>`
    : `<div class="notice"><div class="dot">⏱</div><p><b>No match signals right now.</b> Kalshi limit-order signals appear 24–96 hours before kick-off${opens ? `: from <b>${day(opens)}</b> for gameweek ${gw}` : ""}. ${meta.counts.kalshi_markets_open ? `Kalshi has ${meta.counts.kalshi_markets_open} match prices open.` : "Kalshi has not opened these matches yet."}</p></div>`;
  const finds = lab ? ["maker", "retail", "public_stats"].map((id) => lab.findings.find((f) => f.id === id)).filter(Boolean) : [];
  app.innerHTML = `
    <div class="hero"><h1>${headline}</h1>
      <div class="chips"><span class="chip"><b>${matches.length}</b> fixtures</span>${first ? `<span class="chip">from <b>${day(first)}</b></span>` : ""}<span class="chip"><b>${seasonAll.length}</b> season-market gaps</span></div></div>
    <h2>Live signals <a href="#/lab">How they were tested →</a></h2>
    ${liveHtml}
    ${afconToday(af, clock)}
    ${season.length ? `<h2>Season markets <a href="#/season">All ${seasonAll.length} →</a></h2><div class="grid">${season.map(seasonCard).join("")}</div>` : ""}
    <h2>Fixtures <a href="#/matches">All →</a></h2>
    <div class="grid">${matches.map(fixtureCard).join("")}</div>
    ${finds.length ? `<h2>What the data says <a href="#/lab">The lab →</a></h2><div class="grid">${finds.map(findingCard).join("")}</div>` : ""}`;
  return merge(eplNeeds(md.matches, ss), af ? afconNeeds(af) : {});
}

// ---------------------------------------------------------------------------
// Matches
// ---------------------------------------------------------------------------
async function pageMatches() {
  const [md, ip] = await Promise.all([load("matches.json"), opt("inplay.json")]);
  const byGw = {};
  md.matches.map((m) => eplMatch(m, ip?.clock)).forEach((m) => (byGw[m.gameweek] ||= []).push(m));
  app.innerHTML = Object.entries(byGw).map(([gw, ms]) => `<div class="hero"><h1>Gameweek ${gw}</h1></div><div class="grid">${ms.map(fixtureCard).join("")}</div>`).join("")
    || `<div class="notice"><div class="dot">–</div><p>No fixtures scheduled.</p></div>`;
  return eplNeeds(md.matches, null);
}

async function pageMatch(id) {
  const [md, season, ip] = await Promise.all([load("matches.json"), load("season.json"), opt("inplay.json")]);
  const m0 = md.matches.find((x) => x.match_id === id);
  const m = m0 ? eplMatch(m0, ip?.clock) : null;
  if (!m) { app.innerHTML = `<a class="back" href="#/matches">← Matches</a><div class="notice"><div class="dot">?</div><p>This match is not in the forecast window.</p></div>`; return; }
  const H = fair(m, "1x2", "H"), D = fair(m, "1x2", "D"), A = fair(m, "1x2", "A");
  const venues = [...new Set(m.quotes.map((q) => q.venue))];
  const priceRow = (v) => {
    const cell = (market, sel, line = null) => {
      const q = m.quotes.find((x) => x.venue === v && x.market === market && x.selection === sel && (x.line ?? null) === line);
      if (!q) return `<td class="n muted">–</td>`;
      const p = q.odds ? num(q.odds, 2) : cents(q.ask);
      return `<td class="n" data-tip="${esc(`all-in cost ${cents(q.cost, 1)} · fair ${pct(q.fair, 1)}${q.live ? "\nlive price" : ""}`)}">${p} <span class="${cls(q.edge)} small">${signed(q.edge, 0)}</span></td>`;
    };
    return `<tr><td>${VENUE[v] || v}</td>${cell("1x2", "H")}${cell("1x2", "D")}${cell("1x2", "A")}${cell("total", "over", 2.5)}${cell("total", "under", 2.5)}</tr>`;
  };
  const S = m.score_matrix;
  const tot = new Array(S.length).fill(0);
  S.forEach((r, i) => r.forEach((v, j) => { if (i + j < S.length) tot[i + j] += v; }));
  tot.push(Math.max(0, 1 - tot.reduce((a, b) => a + b, 0)));
  const tmax = Math.max(...tot);
  const team = Object.fromEntries(season.teams.map((t) => [t.team, t]));
  const form = (side) => (m.form[side] || []).map((g) => `<span class="tag ${g.res === "W" ? "hot" : g.res === "L" ? "cold" : ""}" data-tip="${esc(`${g.date} ${g.venue === "H" ? "v" : "at"} ${g.opp}: ${g.gf}–${g.ga}, xG ${num(g.xgf, 1)}–${num(g.xga, 1)}`)}">${g.gf}–${g.ga} ${esc(g.opp)}</span>`).join(" ");
  const groups = [["Result", "1x2"], ["Total goals", "total"], ["Both teams score", "btts"], ["Winning margin", "margin"]];
  const label = (r) => r.market === "1x2" ? (r.selection === "H" ? `${m.home_name} win` : r.selection === "A" ? `${m.away_name} win` : "Draw")
    : r.market === "total" ? `${r.selection} ${r.line}` : r.market === "btts" ? (r.selection === "yes" ? "Both score" : "Not both score")
      : `${r.selection === "home" ? m.home_name : m.away_name} by ${Math.floor(r.line) + 1}+`;
  app.innerHTML = `
    <a class="back" href="#/matches">← Matches</a>
    <div class="card${m.state === "in" ? " is-live" : ""}" style="padding:24px">
      <div class="when"><span>Gameweek ${m.gameweek} · ${esc(kick(m.kickoff_utc))}</span>${m.state === "in" ? `<span class="tag live">live · ${esc(m.detail || m.clock || "")}</span>` : m.state === "post" ? `<span class="tag">full time</span>` : `<span>${m.xg_market ? "model + bookmaker market" : "model only"}</span>`}</div>
      ${teamsRow(m, "", m.state !== "pre" ? `${m.hg}–${m.ag}` : null)}
      ${m.state === "in" && m.now ? `<p class="small soft" style="margin:12px 0 0">Now: home ${pct(m.now[0])} · draw ${pct(m.now[1])} · away ${pct(m.now[2])}, from the score, the clock and the pre-match expected goals. Before kick-off:</p>` : ""}
      ${bar3(m.home, m.away, H.fair, D.fair, A.fair)}
      <div class="pcts" style="font-size:40px"><span>${pct(H.fair)}<small>${esc(m.home_name)}</small></span><span>${pct(D.fair)}<small>draw</small></span><span>${pct(A.fair)}<small>${esc(m.away_name)}</small></span></div>
      <div class="kv"><span>expected goals<b>${num(m.xg_model[0], 2)} – ${num(m.xg_model[1], 2)}</b></span><span>over 2.5<b>${pct(fair(m, "total", "over", 2.5).fair)}</b></span><span>both score<b>${pct(fair(m, "btts", "yes").fair)}</b></span>
        <span>home win, model range<b>${pct(H.model_ci[0])}–${pct(H.model_ci[1])}</b></span></div>
    </div>
    <h2>Prices</h2>
    ${venues.length ? `<div class="tbl"><table><thead><tr><th>venue</th><th class="n">home</th><th class="n">draw</th><th class="n">away</th><th class="n">over 2.5</th><th class="n">under 2.5</th></tr></thead><tbody>${venues.map(priceRow).join("")}</tbody></table></div><p class="small muted">Price, then edge against the fair probability after fees. ${m.state === "pre" ? "Kalshi and Polymarket prices update live." : "Edges are not shown once the match has started: the fair price is a pre-match price."}</p>`
    : `<div class="notice"><div class="dot">–</div><p>No venue has listed this match yet.</p></div>`}
    <div class="grid two" style="margin-top:24px">
      <div class="card"><h3>Scorelines</h3><div style="max-width:360px">${heatmap(S, m.home_code, m.away_code)}</div><p class="small muted">${esc(m.home_code)} goals down, ${esc(m.away_code)} across.</p></div>
      <div class="card"><h3>Total goals</h3>${tot.map((v, k) => `<div class="hbar"><span>${k === tot.length - 1 ? `${k}+` : k} goals</span><span class="track"><i style="width:${(100 * v) / tmax}%;background:var(--blue)"></i></span><span class="v">${pct(v)}</span></div>`).join("")}</div>
    </div>
    <div class="grid two" style="margin-top:14px">
      ${["home", "away"].map((side) => { const t = team[m[side]]; return `<div class="card"><div class="row" style="margin-bottom:10px">${badge(m[side], m[side + "_code"])}<h3 style="margin:0">${esc(m[side + "_name"])}</h3></div>
        ${t ? `<p class="small soft">${t.points_now} pts from ${t.played} · projected ${num(t.exp_points, 0)} · title ${pct(t.title)} · relegation ${pct(t.relegation)}</p>` : ""}
        <div class="row" style="flex-wrap:wrap;gap:6px">${form(side)}</div></div>`; }).join("")}
    </div>
    <details style="margin-top:24px"><summary class="d" style="font-size:20px;cursor:pointer;text-transform:uppercase;font-weight:700">Every market</summary>
      <div class="tbl" style="margin-top:12px"><table><thead><tr><th>selection</th><th class="n">fair</th><th class="n">90% range</th><th class="n">fair odds</th></tr></thead><tbody>
      ${groups.map(([t, key]) => `<tr><td colspan="4" class="muted small" style="padding-top:16px">${t}</td></tr>` + m.markets.filter((r) => r.market === key).map((r) => `<tr><td>${esc(label(r))}</td><td class="n">${pct(r.fair, 1)}</td><td class="n muted">${pct(r.fair_ci[0])}–${pct(r.fair_ci[1])}</td><td class="n">${num(1 / r.fair, 2)}</td></tr>`).join("")).join("")}
      </tbody></table></div></details>`;
  return eplNeeds([m0], null);
}

// ---------------------------------------------------------------------------
// Season
// ---------------------------------------------------------------------------
async function pageSeason() {
  const s = await load("season.json");
  const sig = { season: seasonLive(s.teams) };
  const T = s.teams;
  const mid = (t, mk, v) => { const q0 = t.markets?.[mk]?.[v]; const q = q0 && futuresQuote(q0, v); return q && q.bid != null && q.ask != null ? (q.bid + q.ask) / 2 : null; };
  const race = (mk, filter, title) => {
    const rows = T.filter(filter).sort((a, b) => b[mk] - a[mk]).slice(0, 7);
    return `<div class="card"><h3>${title}</h3>${legend([{ name: "match markets imply", color: "var(--blue)" }, { name: "Kalshi price", color: "var(--ink)" }])}
      ${rows.map((t) => { const k = mid(t, mk, "kalshi"), p = mid(t, mk, "polymarket"); return `<div class="hbar" style="grid-template-columns:130px 1fr 54px"><span class="row" style="gap:6px">${badge(t.team, t.code, "sm")}${esc(t.name)}</span>
        <span class="track" data-tip="${esc(`${t.name}\nmatch markets imply ${pct(t[mk], 1)}${k != null ? `\nKalshi ${cents(k, 1)}` : ""}${p != null ? `\nPolymarket ${cents(p, 1)}` : ""}`)}"><i style="width:${100 * t[mk]}%;background:var(--blue)"></i>${k != null ? `<b style="left:${100 * k}%"></b>` : ""}</span><span class="v">${pct(t[mk])}</span></div>`; }).join("")}</div>`;
  };
  const rows = T.map((t, i) => `<tr><td class="muted">${i + 1}</td><td><span class="team">${badge(t.team, t.code, "sm")}${esc(t.name)}</span></td><td class="n">${t.points_now}</td><td class="n">${num(t.exp_points, 0)}</td>
    <td class="n">${pct(t.title)}</td><td class="n">${pct(t.top4)}</td><td class="n">${pct(t.relegation)}</td><td style="min-width:170px">${posStrip(t.positions, t.name)}</td></tr>`).join("");
  const gaps = sig.season.slice(0, 12).map((x) => `<tr><td><span class="team">${badge(x.team, x.team_code, "sm")}${esc(x.what)}</span></td><td>${esc(x.venue)}</td><td class="n">${cents(x.price)}</td><td class="n">${pct(x.sim)}</td><td class="n"><span class="hl">+${Math.round(100 * (x.sim - x.price))} pts</span></td></tr>`).join("");
  app.innerHTML = `
    <div class="hero"><h1>Season ${esc(s.season)}</h1><div class="chips"><span class="chip"><b>${s.played}</b> played</span><span class="chip"><b>${s.remaining}</b> to go</span><span class="chip"><b>${s.n_sims.toLocaleString()}</b> simulations</span></div></div>
    <div class="grid two">${race("title", (t) => t.title > 0.005, "Title")}${race("relegation", (t) => t.relegation > 0.03, "Relegation")}</div>
    ${gaps ? `<h2>Where season prices disagree</h2><div class="tbl"><table><thead><tr><th>contract</th><th>venue</th><th class="n">price</th><th class="n">match markets imply</th><th class="n">gap</th></tr></thead><tbody>${gaps}</tbody></table></div>` : ""}
    <h2>Projected table</h2>
    <div class="tbl"><table><thead><tr><th></th><th>team</th><th class="n">pts</th><th class="n">proj.</th><th class="n">title</th><th class="n">top 4</th><th class="n">down</th><th>finish 1 → 20</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted">Team strengths are backed out of the closing prices of recent matches, so they reflect what the match market knows. Strengths drift during a season by ${num(s.drift_sd_per_week, 3)} per week (estimated on 2019–24). Kalshi and Polymarket prices update live.</p>`;
  return eplNeeds([], s);
}

// ---------------------------------------------------------------------------
// Lab
// ---------------------------------------------------------------------------
function chartFor(f) {
  const c = f.chart;
  if (!c) return "";
  if (c.type === "bars_ci") return barsCI(c.rows);
  if (c.type === "team_bars") return teamBars(c.rows);
  if (c.type === "pair_bars") return `${legend([{ name: "match-market simulation", color: "var(--blue)" }, { name: "Kalshi season price", color: "var(--draw)" }])}${pairBars(c.rows.map((r) => ({ label: r.label, a: r.sim, b: r.market })))}<p class="small muted">Brier score, lower is better.</p>`;
  if (c.type === "rps") return dotRow(c.rows.map((r) => ({ label: r.label, v: r.rps, hl: r.label.includes("closing") })));
  if (c.type === "rps_pairs") return dotRow(c.rows.flatMap((r) => [{ label: `${r.label}, 1 h out`, v: r.venue_rps, hl: true }, { label: `sharp close (${r.label} matches)`, v: r.close_rps }]));
  return "";
}
function evidenceTable(f) {
  const name = (c) => `${c.side === "bid" ? "bid for" : "offer on"} ${c.group === "draw" ? "draw" : c.group === "big-six" ? "big-six win" : "other-club win"}`;
  return `<div class="tbl"><table><thead><tr><th>order</th><th class="n">placed</th><th class="n">inside by</th><th class="n">filled</th><th class="n">CLV</th><th class="n">90% interval</th><th class="n">ROI</th><th class="n">1st half</th><th class="n">2nd half</th></tr></thead><tbody>
    ${f.evidence.slice().sort((a, b) => b.entry_h - a.entry_h || a.group.localeCompare(b.group)).map((c) => `<tr><td>${name(c)}</td><td class="n">${c.entry_h} h</td><td class="n">${c.improve ? "1¢" : "0"}</td><td class="n">${c.n_filled}</td><td class="n ${cls(c.clv)}">${signed(c.clv)}</td><td class="n muted">${signed(c.clv_ci90[0])} to ${signed(c.clv_ci90[1])}</td><td class="n">${signed(c.roi, 0)}</td><td class="n">${signed(c.clv_discovery)}</td><td class="n">${signed(c.clv_confirmation)}</td></tr>`).join("")}
    </tbody></table></div>`;
}
async function pageLab() {
  const lab = await load("lab.json");
  app.innerHTML = `<div class="hero"><h1>The lab</h1><div class="chips"><span class="chip"><b>${lab.findings.length}</b> hypotheses tested</span><span class="chip"><b>${lab.findings.filter((f) => f.verdict.startsWith("edge")).length}</b> edges found</span></div></div>
    ${lab.findings.map((f) => `<div class="card" style="margin-bottom:14px;padding:24px">
      <div class="grid two" style="align-items:start">
        <div><span class="tag ${VERDICT[f.verdict] ?? ""}">${esc(f.verdict)}</span>${f.venue ? ` <span class="tag">${esc(f.venue)}</span>` : ""}
          <h2 style="margin:14px 0 6px">${esc(f.title)}</h2>
          ${f.number != null ? `<div class="find"><div class="big">${bigNumber(f)}</div><p>${esc(f.number_label)}</p></div>` : ""}
          <p class="soft" style="font-size:15.5px;margin:0 0 12px">${esc(f.summary)}</p>
          ${f.how ? `<p style="margin:0 0 12px"><span class="hl">How to use it</span> ${esc(f.how)}</p>` : ""}
          <details><summary class="small muted" style="cursor:pointer">Method</summary><p class="small soft">${esc(f.method)}</p></details></div>
        <div>${chartFor(f)}</div>
      </div>
      ${f.id === "maker" ? `<details style="margin-top:14px"><summary class="small muted" style="cursor:pointer">Every variant tested</summary><div style="margin-top:10px">${evidenceTable(f)}</div></details>` : ""}
    </div>`).join("")}`;
}

// ---------------------------------------------------------------------------
// Record
// ---------------------------------------------------------------------------
async function pageRecord() {
  const [bt, led] = await Promise.all([load("backtest.json"), opt("ledger.json")]);
  const M = bt.test.models;
  const cur = bt.betting.curves;
  const pick = ["model vs pinnacle, 1x2", "fair vs best price, 1x2"];
  const names = { "model vs pinnacle, 1x2": "our model's picks at Pinnacle", "fair vs best price, 1x2": "best bookmaker price vs fair" };
  const series = pick.filter((k) => cur[k]).map((k, i) => ({ name: names[k], color: i ? "var(--blue)" : "var(--cold)", points: cur[k].date.map((d, j) => [new Date(d).getTime(), cur[k].clv[j]]) }));
  const best = bt.betting.strategies.find((s) => s.label === "fair vs best price, 1x2" && s.threshold === 0.05 && s.staking === "flat");
  const rows = (led?.rows || []).slice().reverse().slice(0, 20);
  app.innerHTML = `
    <div class="hero"><h1>Record</h1><div class="chips"><span class="chip"><b>${bt.test.n.toLocaleString()}</b> held-out matches</span><span class="chip"><b>${led?.n_settled ?? 0}</b> live forecasts settled</span></div></div>
    <div class="grid">
      <div class="card find"><span class="tag cold">model alone</span><div class="big">${num(M.dc.rps, 4)}</div><h3>Our xG model</h3><p>Ranked probability score (lower is better). The closing price scores <b>${num(M.mkt_close.rps, 4)}</b>: the model alone does not beat the sharp close.</p></div>
      <div class="card find"><span class="tag">fair price</span><div class="big">${num(M.fair.rps, 4)}</div><h3>Model + market</h3><p>Matches the pre-match market (<b>${num(M.mkt_pre.rps, 4)}</b>). The model's weight is small because the market knows more.</p></div>
      <div class="card find"><span class="tag hot">best price</span><div class="big">${signed(best.clv)}</div><h3>Beat the close</h3><p>Longest bookmaker price when it beats the fair price by 5%+, ${best.n_bets} bets since 2019.</p></div>
    </div>
    <h2>Closing line value over time</h2>
    <div class="card">${legend(series.map((s) => ({ name: s.name, color: s.color })))}${lines(series, { yFmt: (v) => (v > 0 ? "+" : "") + v.toFixed(0) })}<p class="small muted">Cumulative expected profit at the sharp closing price, 1-unit bets. Rising means beating the market.</p></div>
    <h2>Accuracy by forecast</h2>
    <div class="card">${dotRow(["elo", "dc", "fair", "mkt_pre", "mkt_close"].map((k) => ({ label: { elo: "Elo", dc: "our xG model", fair: "fair price", mkt_pre: "pre-match price", mkt_close: "closing price" }[k], v: M[k].rps, hl: k === "mkt_close" })))}<p class="small muted">Ranked probability score on ${bt.test.n.toLocaleString()} matches, ${esc(bt.test.seasons[0])} onward. Settings chosen on earlier seasons, then frozen.</p></div>
    <h2>Live forecasts</h2>
    <div class="tbl"><table><thead><tr><th>match</th><th>kick-off</th><th class="n">home · draw · away</th><th class="n">written</th><th class="n">result</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.home)} v ${esc(r.away)}</td><td class="muted">${esc(kick(r.kickoff_utc))}</td><td class="n">${Math.round(100 * r.fair_h)} · ${Math.round(100 * r.fair_d)} · ${Math.round(100 * r.fair_a)}</td><td class="n muted">${esc(String(r.first_forecast_utc || "").slice(0, 10))}</td><td class="n">${r.result ? `${r.hg}–${r.ag}` : "–"}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="small muted">Every forecast is committed to the repository before kick-off and frozen at kick-off; the git history is the proof.</p>`;
}

// ---------------------------------------------------------------------------
// Method
// ---------------------------------------------------------------------------
async function pageMethod() {
  app.innerHTML = `<div class="prose">
    <div class="hero"><h1>How it works</h1></div>
    <p>The site looks for places where Premier League prices are systematically wrong, tests each idea on past data, and turns only the ideas that survive into live signals. Everything is computed from public data by code in the repository.</p>
    <h2>What is live</h2>
    <p>This page polls ESPN (scores, match clock, DraftKings odds) every 15 seconds while a match is on and every 2 minutes otherwise, and Polymarket's order books every 30 seconds. Numbers that depend on them are recomputed in the browser: in-play probabilities, price edges after fees, the signal rules and AFCON group odds. The model fits, season and group simulations and bookmaker odds from football-data.co.uk come from a scheduled server run, which GitHub Actions starts every one to six hours; the header shows the age of each source. Kalshi refuses requests from web pages, so its prices come from the server run unless a relay is configured.</p>
    <h2>The benchmark</h2>
    <p>Every idea is judged against the <b>sharp closing price</b>: Pinnacle's or Betfair Exchange's odds just before kick-off with the bookmaker margin removed, the most accurate public forecast of a match. Beating it on average (closing line value) is the standard test of a real edge and far less noisy than profit.</p>
    <h2>How an idea is tested</h2>
    <ul><li>Only information available at the time of the bet is used.</li><li>Every cost is included. Kalshi charges 0.07·p·(1−p) per contract to take a price and 0.0175·p·(1−p) for a resting order on match markets; Polymarket charges rate·p·(1−p) with the rate set per market.</li><li>Rules are found on the first half of the data and must hold on the second half.</li><li>Intervals resample whole match days.</li><li>Ideas whose benchmark depends on a model are not trusted, because the model's own errors can look like an edge.</li></ul>
    <h2>Signals</h2>
    <p><b>Kalshi limit orders.</b> Resting bids for the draw and offers on big-six wins, 24–96 hours before kick-off. In the backtest an order only counts as filled if a later trade printed through its price.</p>
    <p><b>Season markets.</b> Team strengths are backed out of recent matches' closing prices, then the rest of the season is simulated 10,000 times with strengths drifting week to week. Gaps against title, top-four and relegation prices larger than 3 points after costs are listed.</p>
    <p><b>Best bookmaker price.</b> When bookmaker odds are published, the longest price across bookmakers is compared with the fair price.</p>
    <h2>The match model</h2>
    <p>A Dixon–Coles scoreline model fitted to a mix of goals and expected goals, with time decay, prices every market (result, totals, both teams score, margins, correct score) and is blended with bookmaker prices when they exist. It is not more accurate than the closing price, and the record page shows it.</p>
    <h2>Sources</h2>
    <p>football-data.co.uk (results and odds since 2005), Understat (xG since 2014), the Fantasy Premier League API (fixtures), Kalshi and Polymarket (live prices and settled-contract histories).</p>
    <p class="small muted">References: Dixon &amp; Coles (1997); Constantinou &amp; Fenton (2012); Shin (1993); Štrumbelj (2014); Diebold &amp; Mariano (1995); Kaunitz, Zhong &amp; Kreiner (2017).</p>
  </div>`;
}

// ---------------------------------------------------------------------------
const merge = (...ns) => {
  const out = { espn: [], odds: [], poly: [], kalshi: [], kickoffs: [] };
  ns.forEach((n) => Object.keys(out).forEach((k) => out[k].push(...((n && n[k]) || []))));
  return out;
};

// rerender: the live feed or a new server run changed the data; keep the reader's place.
async function route({ rerender = false } = {}) {
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  const r = name || "today";
  const active = r === "match" ? "matches" : r;
  document.querySelectorAll(".tabs a").forEach((a) => { if (a.dataset.r === active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  const y = scrollY, open = rerender ? [...app.querySelectorAll("details")].map((d) => d.open) : [];
  if (!rerender) tip.hidden = true;
  let needs = null;
  try {
    if (r === "match") needs = await pageMatch(decodeURIComponent(arg || ""));
    else if (r === "matches") needs = await pageMatches();
    else if (r === "season") needs = await pageSeason();
    else if (r === "afcon") needs = await pageAfcon(app, load);
    else if (r === "lab") await pageLab();
    else if (r === "record") await pageRecord();
    else if (r === "method") await pageMethod();
    else needs = await pageToday();
  } catch (e) {
    console.error(e);
    if (!rerender) app.innerHTML = `<div class="notice"><div class="dot">!</div><p>Could not load data (${esc(e.message)}).</p></div>`;
    return;
  }
  need(needs || {});
  if (rerender) {
    app.querySelectorAll("details").forEach((d, i) => { if (open[i]) d.open = true; });
    scrollTo(0, y);
  } else scrollTo(0, 0);
  freshness();
}

// Header: how old the newest data on screen is; the tooltip lists every source.
let serverUtc = null;
function freshness() {
  const el = document.getElementById("fresh");
  const now = Date.now();
  const src = { espn: "scores (ESPN)", odds: "DraftKings odds (ESPN)", poly: "Polymarket", kalshi: "Kalshi" };
  const on = Object.entries(live.status).filter(([k, v]) => src[k] && v.ok && v.t && now - v.t < 5 * 60e3);
  const lines = Object.entries(src).map(([k, label]) => {
    const st = live.status[k];
    if (k === "kalshi" && !live.proxy) return `${label}: as of the last server run (Kalshi refuses browser requests; the README explains the relay)`;
    if (!st || !st.t && st.ok !== false) return null;
    return `${label}: ${st.ok ? `${Math.round((now - st.t) / 1000)} s ago` : `not reachable (${st.error})`}`;
  }).filter(Boolean);
  if (serverUtc) lines.push(`model, simulations, bookmaker odds: ${ago(serverUtc)}`);
  if (on.length) {
    const newest = Math.max(...on.map(([, v]) => v.t));
    el.innerHTML = `<i class="pulse"></i>live · ${Math.max(0, Math.round((now - newest) / 1000))} s`;
  } else if (serverUtc) el.innerHTML = `<i></i>updated ${ago(serverUtc)}`;
  el.dataset.tip = lines.join("\n");
}

(async () => {
  let proxy = null;
  try {
    const meta = await load("meta.json");
    version = meta.generated_utc;
    serverUtc = meta.generated_utc;
    proxy = (await opt("live_config.json"))?.kalshi_proxy || null;
  } catch (e) { /* the page shows its own error */ }
  start({
    proxy, version,
    update: () => route({ rerender: true }),
    server: (meta) => { cache.clear(); version = meta.generated_utc; serverUtc = meta.generated_utc; route({ rerender: true }); },
  });
  addEventListener("hashchange", () => route());
  route();
  setInterval(freshness, 1000);
})();

document.addEventListener("pointerover", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) return;
  tip.textContent = t.dataset.tip;
  tip.hidden = false;
  placeTip(tip, e.clientX, e.clientY);
});
document.addEventListener("pointermove", (e) => { if (!tip.hidden) placeTip(tip, e.clientX, e.clientY); });
document.addEventListener("pointerout", (e) => { if (e.target.closest("[data-tip]")) tip.hidden = true; });
document.getElementById("mode").addEventListener("click", () => {
  const d = document.documentElement;
  const dark = d.dataset.theme ? d.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  d.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", d.dataset.theme); } catch (e) {}
  route({ rerender: true });
});
