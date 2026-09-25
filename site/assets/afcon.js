// AFCON 2027 qualifying page and the AFCON block on Today.
import { esc, pct, signed, cents, num, bar3c } from "./charts.js";
import { pairColors } from "./teams.js";

const dtf = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const tf = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dayf = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
const dayKey = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };
const isToday = (iso) => dayKey(iso) === dayKey(new Date().toISOString());

// ESPN serves flags at 500 px; its image resizer returns a 64 px copy (under 1 kB).
export function flag(meta, size = "") {
  const logo = meta?.logo;
  if (!logo) return `<span class="badge ${size}" style="background:var(--card-2);color:var(--ink-2)">${esc(meta?.code || "")}</span>`;
  const src = `https://a.espncdn.com/combiner/i?img=${new URL(logo).pathname}&w=64&h=64`;
  return `<img class="flag ${size}" src="${esc(src)}" alt="" loading="lazy" width="30" height="30" onerror="this.style.visibility='hidden'">`;
}

// ---------------------------------------------------------------------------
// match card
// ---------------------------------------------------------------------------
const FEW = 20;   // fewer international matches than this in eight years: rating is uncertain
export function matchCard(m) {
  const H = m.home_meta || {}, A = m.away_meta || {};
  const [hc, ac] = pairColors(H.color, A.color);
  const live = m.state === "in";
  const mid = live ? `${num(m.live_hg, 0)}–${num(m.live_ag, 0)}` : "v";
  const head = live ? `<span class="tag live">live · ${esc(m.clock || "")}</span>` : `<span>${esc(dtf.format(new Date(m.kickoff_utc)))}</span>`;
  const have = m.p_h != null;
  const bk = m.book;
  const rg = m.range90;
  const tip = have ? `model: home ${pct(m.p_h, 1)} · draw ${pct(m.p_d, 1)} · away ${pct(m.p_a, 1)}${rg ? `\n90% range from rating uncertainty:\nhome ${pct(rg[0][0])}–${pct(rg[0][1])} · draw ${pct(rg[1][0])}–${pct(rg[1][1])} · away ${pct(rg[2][0])}–${pct(rg[2][1])}` : ""}` : "";
  const few = (m.n_matches || []).map((n, i) => [n, i ? m.away : m.home]).filter(([n]) => n < FEW);
  return `<div class="card am${live ? " is-live" : ""}">
    <div class="when">${head}<span>Group ${esc(m.group)}${m.neutral ? " · neutral venue" : ""}</span></div>
    <div class="teams"><div class="t">${flag(H)}<span>${esc(m.home)}</span></div><span class="sc${live ? "" : " v"}">${mid}</span><div class="t r">${flag(A)}<span>${esc(m.away)}</span></div></div>
    ${have ? `<div data-tip="${esc(tip)}">${bar3c(hc, ac, m.p_h, m.p_d, m.p_a)}
    <div class="pcts"><span>${pct(m.p_h)}<small>home</small></span><span>${pct(m.p_d)}<small>draw</small></span><span>${pct(m.p_a)}<small>away</small></span></div></div>
    <div class="kv">${bk ? `<span data-tip="${esc(`${bk.provider} home · draw · away, margin removed (Shin method)${bk.odds ? `\nodds ${bk.odds.map((o) => num(o, 2)).join(" · ")}` : ""}`)}">${esc(bk.provider)}<b>${bk.p.map((p) => Math.round(100 * p)).join(" · ")}</b></span>` : `<span>no bookmaker odds yet</span>`}${m.xg ? `<span>xG<b>${num(m.xg[0], 1)}–${num(m.xg[1], 1)}</b></span>` : ""}${live ? `<span>forecast from before kick-off</span>` : ""}</div>
    ${few.length ? `<p class="small muted" style="margin:10px 0 0">${few.map(([n, t]) => `${esc(t)} has played ${n} internationals in eight years`).join("; ")}: the model knows little, see the range.</p>` : ""}`
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
function groupCard(g, gapKeys) {
  const rows = g.teams.slice().sort((a, b) => b.table.pts - a.table.pts || (b.table.gf - b.table.ga) - (a.table.gf - a.table.ga) || b.table.gf - a.table.gf || b.p_qualify - a.p_qualify);
  const line = (t) => {
    const q = t.market;
    const midp = q && q.bid != null && q.ask != null ? (q.bid + q.ask) / 2 : q?.ask;
    const gap = gapKeys.has(t.team);
    const tip = q ? `${t.team} to win Group ${g.group} on Polymarket\nbid ${cents(q.bid, 1)} · ask ${cents(q.ask, 1)}\n$${Math.round(q.volume).toLocaleString()} traded` : "";
    const pos = t.positions.map((p, i) => `${i + 1}${["st", "nd", "rd", "th"][i]} ${pct(p)}`).join(" · ");
    return `<div class="gr">
      <span class="tm" data-tip="${esc(`${t.team}\n${t.table.w}W ${t.table.d}D ${t.table.l}L, goals ${t.table.gf}–${t.table.ga}\nfinish: ${pos}\nexpected points ${num(t.exp_points, 1)}`)}">${flag(t, "sm")}<span>${esc(t.team)}</span></span>
      <span class="n">${t.table.p}</span><span class="n b">${t.table.pts}</span>
      <span class="q">${t.host ? `<span class="host">host</span>` : `<span class="t"><i style="width:${100 * t.p_qualify}%"></i></span><span class="n">${pct(t.p_qualify)}</span>`}</span>
      <span class="n">${pct(t.p_first)}</span>
      <span class="n${gap ? " hl" : " muted"}"${tip ? ` data-tip="${esc(tip)}"` : ""}>${midp != null ? cents(midp) : "–"}</span>
    </div>`;
  };
  return `<div class="card grp">
    <div class="between" style="margin-bottom:8px"><h3 style="margin:0">Group ${esc(g.group)}</h3>${g.hosts.length ? `<span class="tag">${esc(g.hosts[0])} host: one other place</span>` : ""}</div>
    <div class="gr hd"><span></span><span class="n">P</span><span class="n">Pts</span><span>qualify</span><span class="n">1st</span><span class="n">mkt</span></div>
    ${rows.map(line).join("")}
  </div>`;
}

// ---------------------------------------------------------------------------
// gap card (Polymarket group winner)
// ---------------------------------------------------------------------------
function gapCard(x) {
  return `<div class="card sig">
    <div class="between"><span class="tag">Polymarket · group winner</span><span class="tag">unproven</span></div>
    <div class="row">${flag(x)}<div class="what" style="font-size:22px">${esc(x.what)}</div></div>
    <div class="prices">
      <div><div class="l">${x.side === "yes" ? "buy Yes at" : "buy No at"}</div><div class="v">${cents(x.price)}</div></div>
      <div><div class="l">model</div><div class="v">${pct(x.model)}</div></div>
      <div class="e"><div class="l">gap after fee</div><div class="v edge">+${Math.round(100 * (x.model - x.cost))} pts</div></div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------
export async function pageAfcon(app, load) {
  const [d, bt] = await Promise.all([load("afcon.json"), load("afcon_backtest.json").catch(() => null)]);
  const ms = d.matches;
  const live = ms.filter((m) => m.state === "in");
  const now = Date.now();
  const soon = ms.filter((m) => m.state === "pre" && new Date(m.kickoff_utc) - now < 48 * 3600e3);
  const done = ms.filter((m) => m.state === "post").reverse();
  const later = ms.filter((m) => m.state === "pre" && new Date(m.kickoff_utc) - now >= 48 * 3600e3);
  const byDay = (list) => {
    const out = new Map();
    list.forEach((m) => { const k = dayKey(m.kickoff_utc); if (!out.has(k)) out.set(k, []); out.get(k).push(m); });
    return [...out.values()].map((xs) => `<h3 class="day">${isToday(xs[0].kickoff_utc) ? "Today" : esc(dayf.format(new Date(xs[0].kickoff_utc)))}</h3><div class="grid">${xs.map(matchCard).join("")}</div>`).join("");
  };
  const gaps = d.market_gaps;
  const gapKeys = new Set(gaps.map((x) => x.team));
  const b = bt?.bookmaker, vb = b?.value_bets;
  const rec = d.record;
  const first = ms.length ? new Date(ms[0].kickoff_utc) : null, last = ms.length ? new Date(ms[ms.length - 1].kickoff_utc) : null;
  const df = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
  app.innerHTML = `
    <div class="hero"><h1>${live.length ? `<em>${live.length} live</em> ` : ""}AFCON qualifying</h1>
      <div class="chips">${first ? `<span class="chip">this window <b>${df.format(first)} – ${df.format(last)}</b></span>` : ""}<span class="chip"><b>${ms.filter((m) => m.state === "post").length}</b> of ${ms.length} played</span><span class="chip"><b>${gaps.length}</b> price gaps</span></div></div>

    ${live.length ? `<h2>Live</h2><div class="grid">${live.map(matchCard).join("")}</div>` : ""}

    ${gaps.length ? `<h2>Group-winner prices the model disagrees with</h2>
    <div class="grid">${gaps.slice(0, 3).map(gapCard).join("")}</div>
    <p class="small muted">Polymarket's group-winner markets have traded a few hundred to a few thousand dollars each, so a handful of traders set these prices. The model's group odds come from 10,000 simulations of the remaining fixtures. They have not yet been tested against these prices, so treat the gaps as leads, not signals.${gaps.length > 3 ? ` <a href="#afcon-gaps">All ${gaps.length} ↓</a>` : ""}</p>` : ""}

    <h2>Next 48 hours</h2>
    ${byDay(soon) || `<div class="notice"><div class="dot">–</div><p>No matches in the next two days.</p></div>`}
    ${later.length ? `<details class="more"><summary>Later this window · ${later.length} matches</summary>${byDay(later)}</details>` : ""}

    ${done.length ? `<h2>Results</h2><div class="card results">${done.map(resultRow).join("")}</div>` : ""}

    <h2>Groups <span class="small muted" style="font:500 13px var(--sans);text-transform:none;letter-spacing:0">qualify and 1st: model · mkt: Polymarket, highlighted where it disagrees</span></h2>
    <div class="grid groups">${d.groups.map((g) => groupCard(g, gapKeys)).join("")}</div>

    ${bt ? `<h2>Is the model any good?</h2>
    <div class="grid">
      <div class="card find"><span class="tag hot">beats Elo</span><div class="big">${num(bt.model.rps, 3)}</div><h3>vs ${num(bt.elo.rps, 3)} for Elo</h3><p>Ranked probability score (lower is better) on <b>${bt.n_test}</b> qualifiers since 2022, each forecast using only earlier matches. p = ${num(bt.model_vs_elo.p_value, 2)}.</p></div>
      ${b ? `<div class="card find"><span class="tag">level with the bookmaker</span><div class="big">${num(b.rps_model, 3)}</div><h3>vs ${num(b.rps_book, 3)} for ${esc(b.provider)}</h3><p>On the <b>${b.n}</b> qualifiers of October–November 2024 with closing odds. The difference is noise (p = ${num(b.model_vs_book.p_value, 2)}). The book's margin was ${pct(b.margin, 1)}.</p></div>` : ""}
      ${vb ? `<div class="card find"><span class="tag">not proven</span><div class="big">${signed(vb.roi, 0)}</div><h3>Betting the model's value</h3><p>Return on <b>${vb.n}</b> bets where the model saw 5%+ value at ${esc(b.provider)}. The 90% interval, ${signed(vb.roi_ci90[0], 0)} to ${signed(vb.roi_ci90[1], 0)}, includes losing money.</p></div>` : ""}
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
      <p><b>What it does not know.</b> Squads, injuries, coaching changes and travel. It learns only from results.</p>
      <p class="small muted">Sources: international results compiled by Mart Jürisoo (github.com/martj42/international_results); fixtures, live scores, standings and DraftKings odds from ESPN; Polymarket prices.</p>
    </div></details>`;
}

// ---------------------------------------------------------------------------
// block on the Today page
// ---------------------------------------------------------------------------
export function afconToday(d) {
  if (!d) return "";
  const now = Date.now();
  const ms = d.matches.filter((m) => m.state === "in" || (m.state === "pre" && new Date(m.kickoff_utc) - now < 24 * 3600e3));
  if (!ms.length) return "";
  const live = ms.filter((m) => m.state === "in").length;
  return `<h2>AFCON qualifying${live ? ` <span class="tag live">${live} live</span>` : ""} <a href="#/afcon">Groups and all matches →</a></h2>
    <div class="grid">${ms.slice(0, 6).map(matchCard).join("")}</div>`;
}
