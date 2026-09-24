import {
  esc, pct, pp, signed, num, edgeGlyph, strip, heatmap, posdist, lineChart, bindCharts,
  calibrationChart, rangeRows, dotChart, legend, placeTip,
} from "./charts.js";

const app = document.getElementById("app");
const tip = document.getElementById("tip");
const cache = new Map();
let version = "";

// ---------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------
async function load(name) {
  if (cache.has(name)) return cache.get(name);
  const url = `data/${name}${version ? `?v=${encodeURIComponent(version)}` : ""}`;
  const p = fetch(url, { cache: name === "meta.json" ? "no-store" : "default" }).then((r) => {
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  });
  cache.set(name, p);
  return p;
}

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------
const dtf = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const df = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const kick = (iso) => dtf.format(new Date(iso));
const ago = (iso) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
};
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const VENUE = { kalshi: "Kalshi", polymarket: "Polymarket", betfair: "Betfair Ex.", bet365: "Bet365", "best price": "best price", average: "book average" };
const isPM = (v) => v === "kalshi" || v === "polymarket";

function selectionLabel(q, m) {
  const H = m.home_name ?? m.home, A = m.away_name ?? m.away;
  const byN = (l) => `${Math.floor(l) + 1}+`;
  switch (q.market) {
    case "1x2": return q.selection === "H" ? `${H} win` : q.selection === "A" ? `${A} win` : "draw";
    case "total": return `${q.selection} ${q.line} goals`;
    case "btts": return q.selection === "yes" ? "both teams score" : "not both score";
    case "margin": {
      const side = q.selection.replace("not_", "") === "home" ? H : A;
      return `${q.selection.startsWith("not_") ? "not " : ""}${side} by ${byN(q.line)}`;
    }
    default: return `${q.market} ${q.selection}`;
  }
}
const priceText = (q) => (isPM(q.venue) ? `${pp(q.ask, 1)}¢` : num(q.odds, 2));

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------
function setStatus(meta) {
  const s = meta.sources || {};
  const bits = [`updated ${new Date(meta.generated_utc).toISOString().slice(11, 16)} UTC (${ago(meta.generated_utc)})`,
    `results to ${df.format(new Date(meta.model.last_result_utc))}`];
  const short = {
    "football-data.co.uk results": "results", "fantasy.premierleague.com fixtures": "fixtures",
    "football-data.co.uk odds": "books", "kalshi match markets": "kalshi", "polymarket match markets": "polymarket",
    "kalshi season markets": "kalshi season", "polymarket season markets": "polymarket season",
  };
  for (const [k, v] of Object.entries(s)) {
    const n = v.n != null ? ` ${v.n}` : "";
    bits.push(`<span class="${v.ok ? "ok" : "bad"}" title="${esc(v.error || k)}">${esc(short[k] || k)}${n}</span>`);
  }
  document.getElementById("status").innerHTML = bits.join(" · ");
}

function setNav(route) {
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.route === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function secHead(title, aside = "", right = "") {
  return `<div class="sec-head"><h2>${title}</h2>${aside ? `<span class="aside">${aside}</span>` : ""}${right ? `<span class="aside right">${right}</span>` : ""}</div>`;
}

// ---------------------------------------------------------------------------
// board
// ---------------------------------------------------------------------------
const boardState = { venue: "all", market: "all", minP: 0, positive: true };

async function pageBoard() {
  const [meta, board, mdata, season] = await Promise.all([load("meta.json"), load("board.json"), load("matches.json"), load("season.json")]);
  const matches = mdata.matches;
  const byId = Object.fromEntries(matches.map((m) => [m.match_id, m]));
  const gws = [...new Set(matches.map((m) => m.gameweek))];
  const first = matches[0], last = matches[matches.length - 1];
  const nQuotes = matches.reduce((a, m) => a + m.quotes.length, 0);
  const qByVenue = {};
  matches.forEach((m) => m.quotes.forEach((q) => { qByVenue[q.venue] = (qByVenue[q.venue] || 0) + 1; }));
  const withBook = matches.filter((m) => m.xg_market).length;
  const opps = board.opportunities;
  const posOpps = opps.filter((o) => o.edge > 0);

  let summary = "";
  if (matches.length) {
    summary = `${matches.length} fixtures from ${kick(first.kickoff_utc)} to ${kick(last.kickoff_utc)}. `;
    summary += nQuotes
      ? `${nQuotes} prices collected (${Object.entries(qByVenue).map(([v, n]) => `${VENUE[v] || v} ${n}`).join(", ")}). `
      : "No venue has priced these matches yet: Kalshi and Polymarket have opened EPL match markets about two weeks before kick-off this season, and football-data.co.uk publishes bookmaker odds on Tuesdays and Fridays. ";
    summary += withBook
      ? `${withBook} of ${matches.length} fair prices are anchored to a bookmaker price; the rest are the model alone.`
      : "Until bookmaker odds arrive, every fair price below is the model alone.";
  }

  const alpha = meta.model.alpha;
  const html = `
    <div class="lede">
      <div>
        <div class="kicker">premier league ${esc(meta.season)} · gameweek ${gws.join("–")}</div>
        <h1>Model and market prices for the next fixtures</h1>
        <p>${esc(summary)}</p>
      </div>
    </div>
    <div class="stats">
      <div class="stat"><div class="v">${matches.length}</div><div class="l">fixtures priced</div></div>
      <div class="stat"><div class="v">${nQuotes}</div><div class="l">venue prices compared</div></div>
      <div class="stat"><div class="v">${posOpps.length}</div><div class="l">prices below fair value</div></div>
      <div class="stat"><div class="v">${Math.round(alpha * 100)}%</div><div class="l">model weight in fair price</div></div>
    </div>
    <section id="opps">${oppsSection(opps, byId)}</section>
    <section>
      ${secHead("fixtures", "fair probabilities: home · draw · away", `<a href="#/method">how the fair price is made</a>`)}
      ${fixturesTable(matches)}
    </section>
    <section>
      ${secHead("season markets", "largest gaps between the season simulation and venue prices")}
      ${seasonGaps(season)}
    </section>`;
  app.innerHTML = html;
  bindBoardFilters(opps, byId);
}

function oppsSection(opps, byId) {
  const venues = [...new Set(opps.map((o) => o.venue))];
  const markets = [...new Set(opps.map((o) => o.market))];
  const s = boardState;
  const rows = opps.filter((o) => (s.venue === "all" || o.venue === s.venue) && (s.market === "all" || o.market === s.market)
    && (o.p_edge_pos ?? 0) >= s.minP && (!s.positive || o.edge > 0));
  const chips = (key, vals, label) => `<div class="grp"><span>${label}</span>${vals.map((v) =>
    `<button class="chip" data-k="${key}" data-v="${esc(v)}" aria-pressed="${String(s[key]) === String(v)}">${esc(v === "all" ? "all" : VENUE[v] || v)}</button>`).join("")}</div>`;
  const filt = opps.length ? `<div class="filters">
      ${chips("venue", ["all", ...venues], "venue")}
      ${chips("market", ["all", ...markets], "market")}
      ${chips("minP", [0, 0.5, 0.8, 0.9], "p(edge>0) ≥")}
      <div class="grp"><button class="chip" data-k="positive" data-v="toggle" aria-pressed="${s.positive}">only positive edge</button></div>
    </div>` : "";
  let body;
  if (!opps.length) {
    body = `<div class="empty">No prices to compare yet. This table fills as soon as Kalshi, Polymarket or the bookmakers list these matches. Each row is a price you can take now, compared with the fair probability: the edge is fair probability divided by the all-in cost of the contract (price plus fees), minus one.</div>`;
  } else if (!rows.length) {
    body = `<div class="empty">No prices match these filters.</div>`;
  } else {
    body = `<div class="scroll"><table class="t">
      <thead><tr><th class="first">match</th><th>selection</th><th>venue</th><th class="n">price</th><th class="n">fair</th><th>edge, 90% interval</th><th class="n">edge</th><th class="n">p(edge>0)</th><th class="n">¼ kelly</th><th class="n">volume</th></tr></thead>
      <tbody>${rows.slice(0, 60).map((o) => {
        const m = byId[o.match_id] || o;
        const mk = (m.markets || []).find((x) => x.market === o.market && x.selection === o.selection && (x.line ?? null) === (o.line ?? null));
        const ci = mk ? mk.fair_ci : [o.fair, o.fair];
        return `<tr class="link" data-href="#/match/${esc(o.match_id)}">
          <td class="first nowrap"><span class="code">${esc(o.home_code)}–${esc(o.away_code)}</span> <span class="dim">${esc(kick(o.kickoff_utc))}</span></td>
          <td class="nowrap">${esc(selectionLabel(o, m))}</td>
          <td>${esc(VENUE[o.venue] || o.venue)}</td>
          <td class="n" data-tip="${esc(`all-in cost ${pp(o.cost, 2)}¢ per $1 contract`)}">${priceText(o)}</td>
          <td class="n">${pct(o.fair)}</td>
          <td>${edgeGlyph({ lo: ci[0], p: o.fair, hi: ci[1], cost: o.cost })}</td>
          <td class="n ${cls(o.edge)}">${signed(o.edge)}</td>
          <td class="n">${o.p_edge_pos != null ? pp(o.p_edge_pos, 0) + "%" : "–"}</td>
          <td class="n">${o.edge > 0 ? pct(o.kelly / 4, 1) : "–"}</td>
          <td class="n dim">${o.volume ? Math.round(o.volume).toLocaleString() : "–"}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }
  return `${secHead("value board", "every venue price against the fair probability, largest edge first")}${filt}${body}
    <p class="note">Edge is expected profit per $1 staked at the quoted price after fees (Kalshi 0.07·p·(1−p) per contract; Polymarket rate·p·(1−p)). <b>p(edge>0)</b> is the share of parameter draws in which the fair probability exceeds the break-even price. The backtest found that edges against Kalshi and Polymarket measured this way did not survive to the close; see <a href="#/record">record</a>.</p>`;
}

function bindBoardFilters(opps, byId) {
  const root = document.getElementById("opps");
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest(".chip");
    if (b) {
      const k = b.dataset.k;
      if (k === "positive") boardState.positive = !boardState.positive;
      else boardState[k] = k === "minP" ? Number(b.dataset.v) : b.dataset.v;
      root.innerHTML = oppsSection(opps, byId);
      return;
    }
  });
}

function fixturesTable(matches) {
  if (!matches.length) return `<div class="empty">No upcoming fixtures in the FPL schedule.</div>`;
  const get = (m, market, sel, line = null) => (m.markets.find((x) => x.market === market && x.selection === sel && (x.line ?? null) === line) || {}).fair;
  return `<div class="scroll"><table class="t">
    <thead><tr><th class="first">match</th><th style="min-width:140px">home · draw · away</th><th class="n hide-sm">xG</th><th class="n hide-sm">over 2.5</th><th class="n hide-sm">both score</th><th class="hide-sm">fair price basis</th></tr></thead>
    <tbody>${matches.map((m) => {
      const h = get(m, "1x2", "H"), d = get(m, "1x2", "D"), a = get(m, "1x2", "A");
      return `<tr class="link" data-href="#/match/${esc(m.match_id)}">
        <td class="first team">${esc(m.home_name)} <span class="dim">v</span> ${esc(m.away_name)}<span class="sub-line">${esc(kick(m.kickoff_utc))}</span></td>
        <td>${strip(h, d, a)}</td>
        <td class="n hide-sm">${num(m.xg_model[0], 2)}–${num(m.xg_model[1], 2)}</td>
        <td class="n hide-sm">${pct(get(m, "total", "over", 2.5), 0)}</td>
        <td class="n hide-sm">${pct(get(m, "btts", "yes"), 0)}</td>
        <td class="dim hide-sm">${m.xg_market ? "model + bookmaker" : "model only"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function seasonGaps(season) {
  const rows = [];
  const names = { title: "title", top4: "top 4", relegation: "relegated" };
  season.teams.forEach((t) => {
    for (const mk of ["title", "top4", "relegation"]) {
      for (const [venue, q] of Object.entries(t.markets[mk] || {})) {
        if (q.bid == null || q.ask == null || q.ask - q.bid > 0.05) continue;   // skip illiquid books
        const mid = (q.bid + q.ask) / 2;
        rows.push({ team: t, mk, venue, mid, model: t[mk], gap: t[mk] - mid, q });
      }
    }
  });
  rows.sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap));
  if (!rows.length) return `<div class="empty">No liquid season markets found.</div>`;
  return `<div class="scroll"><table class="t">
    <thead><tr><th class="first">team</th><th>market</th><th>venue</th><th class="n hide-sm">bid–ask</th><th class="n">model</th><th class="n">model − mid</th></tr></thead>
    <tbody>${rows.slice(0, 10).map((r) => `<tr>
      <td class="first team">${esc(r.team.name)}</td><td>${names[r.mk]}</td><td>${esc(VENUE[r.venue])}</td>
      <td class="n hide-sm">${pp(r.q.bid, 1)}–${pp(r.q.ask, 1)}¢</td><td class="n">${pct(r.model)}</td>
      <td class="n ${cls(r.gap)}">${r.gap > 0 ? "+" : "−"}${pp(Math.abs(r.gap), 1)} pts</td></tr>`).join("")}</tbody></table></div>
    <p class="note">The season simulation uses the model alone (${season.n_sims.toLocaleString()} simulated seasons with parameter uncertainty). There is no sharp bookmaker anchor for season markets, and the model is less accurate than the market at the match level, so a gap here is a question to investigate, not a bet. Markets with a bid–ask spread wider than 5¢ are left out.</p>`;
}

// ---------------------------------------------------------------------------
// match
// ---------------------------------------------------------------------------
async function pageMatch(id) {
  const [meta, mdata, season] = await Promise.all([load("meta.json"), load("matches.json"), load("season.json")]);
  const m = mdata.matches.find((x) => x.match_id === id);
  if (!m) { app.innerHTML = `<a class="back" href="#/">← board</a><div class="empty">This match is not in the current forecast window.</div>`; return; }
  const teamRow = Object.fromEntries(season.teams.map((t) => [t.team, t]));
  const mk = (market, sel, line = null) => m.markets.find((x) => x.market === market && x.selection === sel && (x.line ?? null) === line) || {};
  const q1 = (venue, sel) => m.quotes.find((q) => q.venue === venue && q.market === "1x2" && q.selection === sel);
  const H = mk("1x2", "H"), D = mk("1x2", "D"), A = mk("1x2", "A");
  const book = m.book || {};

  const venueLine = (sel) => {
    const out = [];
    if (book.book_h != null) out.push(`book ${pp(book[`book_${sel.toLowerCase()}`], 0)}`);
    for (const v of ["kalshi", "polymarket"]) {
      const q = q1(v, sel);
      if (q && q.bid != null && q.ask != null) out.push(`${v === "kalshi" ? "K" : "P"} ${pp((q.bid + q.ask) / 2, 0)}`);
    }
    return out.join(" · ");
  };
  const block = (label, x, sel) => `<div><div class="l">${label}</div><div class="v">${pct(x.fair, 0)}</div>
    <div class="s">model ${pct(x.model, 0)} [${pp(x.model_ci[0], 0)}–${pp(x.model_ci[1], 0)}]</div>
    <div class="s">${venueLine(sel) || "&nbsp;"}</div></div>`;

  // total goals distribution from the (truncated) score matrix
  const S = m.score_matrix;
  const tot = new Array(2 * S.length - 1).fill(0);
  S.forEach((r, i) => r.forEach((v, j) => { tot[i + j] += v; }));
  const totDist = tot.slice(0, S.length);            // exact for totals below the truncation
  totDist.push(1 - totDist.reduce((a, b) => a + b, 0));
  const tmax = Math.max(...totDist);

  // all-markets table
  const groups = [["result", "1x2"], ["total goals", "total"], ["both teams score", "btts"], ["winning margin", "margin"]];
  const quoteCells = (row) => {
    const qs = m.quotes.filter((q) => q.market === row.market && q.selection === row.selection && (q.line ?? null) === (row.line ?? null));
    if (!qs.length) return `<td class="dim">–</td>`;
    return `<td>${qs.map((q) => `<span class="nowrap" data-tip="${esc(`${VENUE[q.venue]}: price ${priceText(q)}, all-in cost ${pp(q.cost, 2)}¢\nedge vs fair ${signed(q.edge)}${q.p_edge_pos != null ? `, p(edge>0) ${pp(q.p_edge_pos, 0)}%` : ""}`)}">${esc(VENUE[q.venue])} ${priceText(q)} <span class="${cls(q.edge)}">${signed(q.edge, 0)}</span></span>`).join("<br>")}</td>`;
  };
  const mt = groups.map(([title, key]) => {
    const rows = m.markets.filter((r) => r.market === key);
    return `<tr class="group"><td colspan="5">${title}</td></tr>` + rows.map((r) => `<tr>
      <td class="first">${esc(selectionLabel(r, m))}</td>
      <td class="n">${pct(r.fair, 1)} <span class="ci">${pp(r.fair_ci[0], 0)}–${pp(r.fair_ci[1], 0)}</span></td>
      <td class="n">${pct(r.model, 1)}</td>
      <td class="n dim">${r.fair > 0 ? num(1 / r.fair, 2) : "–"}</td>
      ${quoteCells(r)}</tr>`).join("");
  }).join("");

  const teamBlock = (code, name, side) => {
    const t = teamRow[m[side]];
    const f = m.form[side] || [];
    const r = t?.rating;
    return `<div>
      <h3>${esc(name)}</h3>
      <p class="note">${r ? `vs an average team at a neutral ground: <b>${num(r.xgf, 2)}</b> xG for [${num(r.xgf_ci[0], 2)}–${num(r.xgf_ci[1], 2)}], <b>${num(r.xga, 2)}</b> against [${num(r.xga_ci[0], 2)}–${num(r.xga_ci[1], 2)}]` : ""}${t ? `. Season: ${t.points_now} pts from ${t.played}, projected ${num(t.exp_points, 0)} ± ${num(t.sd_points, 0)}.` : ""}</p>
      <div class="form">${f.map((g) => `<span class="${g.res}" data-tip="${esc(`${g.date} ${g.venue === "H" ? "v" : "at"} ${g.opp}\n${g.gf}–${g.ga}, xG ${num(g.xgf, 2)}–${num(g.xga, 2)}`)}"><b>${g.gf}–${g.ga}</b>${esc(g.opp)}</span>`).join("")}</div>
    </div>`;
  };

  app.innerHTML = `
    <a class="back" href="#/">← board</a>
    <div class="fixture-head">
      <div class="side">${esc(m.home_name)}<span class="xg">xG ${num(m.xg_model[0], 2)} [${num(m.xg_model_ci[0][0], 2)}–${num(m.xg_model_ci[0][1], 2)}]${m.xg_market ? ` · market ${num(m.xg_market[0], 2)}` : ""}</span></div>
      <div class="mid">gameweek ${m.gameweek}<br>${esc(kick(m.kickoff_utc))}</div>
      <div class="side away">${esc(m.away_name)}<span class="xg">xG ${num(m.xg_model[1], 2)} [${num(m.xg_model_ci[1][0], 2)}–${num(m.xg_model_ci[1][1], 2)}]${m.xg_market ? ` · market ${num(m.xg_market[1], 2)}` : ""}</span></div>
    </div>
    <div class="three">${block(`${m.home_code} win`, H, "H")}${block("draw", D, "D")}${block(`${m.away_code} win`, A, "A")}</div>
    <p class="note">Large numbers are the fair probability; basis: ${esc(m.fair_basis)}. Brackets are the model's 90% interval from parameter uncertainty (not match randomness). K = Kalshi mid, P = Polymarket mid, book = de-margined bookmaker price.</p>
    <div class="cols" style="margin-top:28px">
      <section>
        ${secHead("correct score", `${m.home_code} goals down, ${m.away_code} across`)}
        <div style="max-width:380px;margin-top:10px">${heatmap(S, m.home_code, m.away_code)}</div>
        <p class="note">Scores beyond 6 goals for either side: ${pct(m.score_matrix_rest, 2)}.</p>
      </section>
      <section>
        ${secHead("total goals")}
        <div style="margin-top:10px">${totDist.map((v, k) => `<div style="display:grid;grid-template-columns:28px 1fr 52px;gap:8px;align-items:center;margin:3px 0" data-tip="${esc(`${k === totDist.length - 1 ? `${k}+` : k} goals: ${pct(v, 1)}`)}">
          <span class="mono muted">${k === totDist.length - 1 ? `${k}+` : k}</span>
          <span style="height:12px;background:var(--s1);width:${(100 * v) / tmax}%;border-radius:0 3px 3px 0"></span>
          <span class="num soft" style="text-align:right">${pct(v, 1)}</span></div>`).join("")}</div>
      </section>
    </div>
    <section>
      ${secHead("every market", "fair probability [90% interval] · model · fair decimal odds · venue prices with edge")}
      <div class="scroll"><table class="t">
        <thead><tr><th class="first">selection</th><th class="n">fair</th><th class="n">model</th><th class="n">fair odds</th><th>venues</th></tr></thead>
        <tbody>${mt}</tbody></table></div>
    </section>
    <section>
      ${secHead("teams", "last six league matches: score and opponent; hover for xG")}
      <div class="cols" style="margin-top:12px">${teamBlock(m.home_code, m.home_name, "home")}${teamBlock(m.away_code, m.away_name, "away")}</div>
    </section>`;
}

// ---------------------------------------------------------------------------
// season
// ---------------------------------------------------------------------------
async function pageSeason() {
  const season = await load("season.json");
  const t = season.teams;
  const mq = (team, mk) => {
    const out = [];
    for (const [v, q] of Object.entries(team.markets[mk] || {})) {
      if (q.bid == null || q.ask == null) continue;
      out.push(`<span data-tip="${esc(`${VENUE[v]} bid ${pp(q.bid, 1)}¢ ask ${pp(q.ask, 1)}¢`)}">${v === "kalshi" ? "K" : "P"} ${pp((q.bid + q.ask) / 2, 0)}</span>`);
    }
    return out.length ? `<div class="ci">${out.join(" · ")}</div>` : "";
  };
  const rows = t.map((x, i) => `<tr>
    <td class="n dim">${i + 1}</td>
    <td class="first team">${esc(x.name)}</td>
    <td class="n">${x.played}</td><td class="n">${x.points_now}</td><td class="n">${x.gd_now > 0 ? "+" : ""}${x.gd_now}</td>
    <td class="n">${num(x.exp_points, 1)} <span class="ci">±${num(x.sd_points, 0)}</span></td>
    <td class="n">${pct(x.title, 1)}${mq(x, "title")}</td>
    <td class="n">${pct(x.top4, 1)}${mq(x, "top4")}</td>
    <td class="n">${pct(x.relegation, 1)}${mq(x, "relegation")}</td>
    <td style="min-width:170px">${posdist(x.positions, x.name)}</td></tr>`).join("");
  const rr = [...t].sort((a, b) => b.rating.net - a.rating.net).map((x) => ({
    label: x.code, v: x.rating.net, lo: x.rating.net_ci[0], hi: x.rating.net_ci[1],
    tip: `${x.name}\nxG for ${num(x.rating.xgf, 2)}, against ${num(x.rating.xga, 2)} per match\nnet ${num(x.rating.net, 2)} [${num(x.rating.net_ci[0], 2)}, ${num(x.rating.net_ci[1], 2)}]`,
  }));
  app.innerHTML = `
    <div class="lede"><div>
      <div class="kicker">premier league ${esc(season.season)} · ${season.played} played, ${season.remaining} to play</div>
      <h1>Season simulation</h1>
      <p>${season.n_sims.toLocaleString()} simulated seasons. Each simulation draws team strengths from the model's parameter uncertainty, then plays every remaining fixture from the scoreline model. Small grey figures are venue mid prices (K = Kalshi, P = Polymarket).</p>
    </div></div>
    <section>
      ${secHead("table", "sorted by projected points")}
      <div class="scroll"><table class="t">
        <thead><tr><th class="n"></th><th class="first">team</th><th class="n">P</th><th class="n">pts</th><th class="n">GD</th><th class="n">proj. pts</th><th class="n">title</th><th class="n">top 4</th><th class="n">relegated</th><th>finishing position 1 → 20</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </section>
    <section>
      ${secHead("team strength", "expected goal difference per match against an average team at a neutral ground, 90% interval")}
      <figure style="max-width:620px;margin-top:10px">${rangeRows({ rows: rr, fmt: (v) => (v > 0 ? "+" : "") + v.toFixed(2), title: "team strength" })}</figure>
    </section>`;
}

// ---------------------------------------------------------------------------
// record
// ---------------------------------------------------------------------------
const MODEL_NAMES = { elo: "Elo baseline", dc: "model (Dixon–Coles, xG)", pool: "model–market pool", fair: "fair price", mkt_pre: "bookmaker, pre-match", mkt_close: "bookmaker, closing" };

async function pageRecord() {
  const [bt, ven, led] = await Promise.all([load("backtest.json"), load("venues.json").catch(() => null), load("ledger.json").catch(() => null)]);
  const T = bt.test;
  const M = T.models;
  const order = ["elo", "dc", "pool", "fair", "mkt_pre", "mkt_close"];
  const cfg = bt.config;

  const metricsTable = `<div class="scroll"><table class="t">
    <thead><tr><th class="first">forecast</th><th class="n">RPS</th><th class="n">90% interval</th><th class="n">log loss</th><th class="n">Brier</th><th class="n">accuracy</th><th class="n">RPS − closing</th><th class="n">p</th></tr></thead>
    <tbody>${order.map((k) => {
      const v = M[k], dm = T.dm_vs_close[k];
      return `<tr><td class="first">${MODEL_NAMES[k]}</td><td class="n">${num(v.rps, 4)}</td><td class="n ci">${num(v.rps_ci90[0], 4)}–${num(v.rps_ci90[1], 4)}</td>
        <td class="n">${num(v.log_loss, 4)}</td><td class="n">${num(v.brier, 4)}</td><td class="n">${pct(v.accuracy, 1)}</td>
        <td class="n">${dm ? (dm.mean_diff > 0 ? "+" : "") + num(dm.mean_diff, 4) : "–"}</td><td class="n">${dm ? (dm.p_value < 0.001 ? "<0.001" : num(dm.p_value, 3)) : "–"}</td></tr>`;
    }).join("")}</tbody></table></div>`;

  const per = T.per_season;
  const perChart = dotChart({
    cats: per.map((r) => r.season.slice(2)),
    series: [{ name: MODEL_NAMES.dc, color: "var(--s1)", values: per.map((r) => r.dc) },
      { name: MODEL_NAMES.fair, color: "var(--s3)", values: per.map((r) => r.fair) },
      { name: MODEL_NAMES.mkt_close, color: "var(--s2)", values: per.map((r) => r.mkt_close) }],
    yFmt: (v) => v.toFixed(3), title: "RPS by season",
  });

  const cal = bt.calibration;
  const calChart = calibrationChart({ series: [{ name: MODEL_NAMES.dc, color: "var(--s1)", bins: cal.dc.bins }, { name: MODEL_NAMES.mkt_close, color: "var(--s2)", bins: cal.mkt_close.bins }], lo: 0, hi: 0.9, title: "calibration, home/draw/away" });
  const calOU = calibrationChart({ series: [{ name: MODEL_NAMES.dc, color: "var(--s1)", bins: cal.dc_o25.bins }, { name: MODEL_NAMES.mkt_close, color: "var(--s2)", bins: cal.mkt_close_o25.bins }], lo: 0.2, hi: 0.8, title: "calibration, over 2.5 goals" });

  const enc = bt.encompassing;
  const encRow = (label, e) => `<tr><td class="first">${label}</td><td class="n">${num(e.a, 3)} <span class="ci">± ${num(e.se_a, 3)}</span></td><td class="n">${num(e.b, 3)} <span class="ci">± ${num(e.se_b, 3)}</span></td><td class="n">${e.n}</td></tr>`;

  // betting
  const strat = bt.betting.strategies.filter((s) => s.staking === "flat" && (s.threshold === 0.02 || s.threshold === 0.05) && s.n_bets);
  const stratTable = `<div class="scroll"><table class="t">
    <thead><tr><th class="first">strategy</th><th class="n">min edge</th><th class="n">bets</th><th class="n">ROI</th><th class="n">90% interval</th><th class="n">CLV</th><th class="n">90% interval</th><th class="n">mean odds</th></tr></thead>
    <tbody>${strat.map((s) => `<tr><td class="first">${esc(s.label)}</td><td class="n">${pct(s.threshold, 0)}</td><td class="n">${s.n_bets.toLocaleString()}</td>
      <td class="n ${cls(s.roi)}">${signed(s.roi)}</td><td class="n ci">${signed(s.roi_ci90[0])} to ${signed(s.roi_ci90[1])}</td>
      <td class="n ${cls(s.clv)}">${signed(s.clv)}</td><td class="n ci">${s.clv_ci90 && s.clv_ci90[0] != null ? `${signed(s.clv_ci90[0])} to ${signed(s.clv_ci90[1])}` : "–"}</td>
      <td class="n">${num(s.mean_odds, 2)}</td></tr>`).join("")}</tbody></table></div>`;
  const curves = bt.betting.curves;
  const colors = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
  const cNames = Object.keys(curves);
  const toPts = (c, key) => c.date.map((d, i) => [new Date(d).getTime(), c[key][i]]);
  const profitChart = lineChart({ series: cNames.map((n, i) => ({ name: n, label: n.replace(", ", " "), color: colors[i], points: toPts(curves[n], "profit") })), xType: "date", yFmt: (v) => (v > 0 ? "+" : "") + v.toFixed(0), width: 720, height: 250, title: "cumulative profit, units" });
  const clvChart = lineChart({ series: cNames.map((n, i) => ({ name: n, label: n.replace(", ", " "), color: colors[i], points: toPts(curves[n], "clv") })), xType: "date", yFmt: (v) => (v > 0 ? "+" : "") + v.toFixed(0), width: 720, height: 250, title: "cumulative closing line value, units" });

  const buckets = (label) => {
    const rows = bt.betting.edge_buckets[label] || [];
    return `<div class="scroll"><table class="t"><thead><tr><th class="first">claimed edge</th><th class="n">bets</th><th class="n">ROI</th><th class="n">90% interval</th><th class="n">CLV</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><td class="first">${pct(r.edge_lo, 0)} to ${r.edge_hi == null ? "∞" : pct(r.edge_hi, 0)}</td><td class="n">${r.n}</td><td class="n ${cls(r.roi)}">${signed(r.roi)}</td>
        <td class="n ci">${signed(r.roi_ci90[0])} to ${signed(r.roi_ci90[1])}</td><td class="n ${cls(r.clv)}">${signed(r.clv)}</td></tr>`).join("")}</tbody></table></div>`;
  };

  const unc = bt.uncertainty;

  app.innerHTML = `
    <div class="lede"><div>
      <div class="kicker">walk-forward backtest · ${T.n.toLocaleString()} held-out matches · ${esc(T.seasons[0])} to ${esc(T.seasons[T.seasons.length - 1])}</div>
      <h1>Track record</h1>
      <p>Every forecast below was made by a model fitted only on matches played before the forecast date, with settings chosen on ${esc(bt.protocol.validation_seasons.join(", "))} and then frozen. Ranked probability score (RPS) is the main measure; lower is better. On these matches the model scores ${num(M.dc.rps, 4)} and the closing bookmaker price ${num(M.mkt_close.rps, 4)}: <b>the model alone does not beat the market.</b> The fair price, which blends the two, matches the pre-match market (${num(M.fair.rps, 4)} vs ${num(M.mkt_pre.rps, 4)}).</p>
    </div></div>

    <section>${secHead("accuracy", "home / draw / away, all test seasons")}${metricsTable}
      <p class="note">Intervals resample whole match days (cluster bootstrap). The p value is a Diebold–Mariano test of equal RPS against the closing price, with standard errors clustered by match day.</p></section>

    <section>${secHead("by season")}${legend([{ name: MODEL_NAMES.dc, color: "var(--s1)", dot: 1 }, { name: MODEL_NAMES.fair, color: "var(--s3)", dot: 1 }, { name: MODEL_NAMES.mkt_close, color: "var(--s2)", dot: 1 }])}<figure>${perChart}</figure></section>

    <section>${secHead("calibration", "when a forecast says x%, how often does it happen?")}
      ${legend([{ name: MODEL_NAMES.dc, color: "var(--s1)", dot: 1 }, { name: MODEL_NAMES.mkt_close, color: "var(--s2)", dot: 1 }])}
      <div class="cols">
        <figure style="max-width:420px">${calChart}<figcaption>Home, draw and away probabilities pooled. Expected calibration error: model ${pct(cal.dc.ece, 2)}, closing price ${pct(cal.mkt_close.ece, 2)}. Whiskers are 90% Wilson intervals.</figcaption></figure>
        <figure style="max-width:420px">${calOU}<figcaption>Over 2.5 goals. Expected calibration error: model ${pct(cal.dc_o25.ece, 2)}, closing price ${pct(cal.mkt_close_o25.ece, 2)}.</figcaption></figure>
      </div></section>

    <section>${secHead("does the model know anything the market does not?", "encompassing test")}
      <div class="scroll"><table class="t"><thead><tr><th class="first">combination</th><th class="n">weight on model</th><th class="n">weight on market</th><th class="n">matches</th></tr></thead>
      <tbody>${encRow("model + closing price, 1X2", enc.vs_closing_1x2)}${encRow("model + pre-match price, 1X2", enc.vs_pre_1x2)}${encRow("model + closing price, over 2.5", enc.vs_closing_ou25)}</tbody></table></div>
      <p class="note">Weights of a logarithmic pool p ∝ model<sup>a</sup> · market<sup>b</sup> fitted by maximum likelihood (± one standard error). If the model carried information the market had not priced, its weight would be clearly above zero. It is not: the weights are within about one standard error of zero. The live fair price uses a blend weight of ${pct(cfg.live.alpha, 0)} on the model, fitted on ${esc(cfg.live.fitted_on.join(" to "))}.</p></section>

    <section>${secHead("betting simulation", "one bet per match on the largest edge, flat 1-unit stakes, pre-match prices")}
      ${stratTable}
      <p class="note"><b>CLV</b> (closing line value) is the expected return of each bet under the de-margined closing price. It is a far less noisy measure of skill than profit. Two findings: bets chosen by the model against Pinnacle have clearly negative CLV (about −4%), so the model's disagreements with a sharp book are mostly model error. Bets where the fair price says the <b>best price across bookmakers</b> is too long have positive CLV, which is the known price-dispersion effect (Kaunitz, Zhong & Kreiner 2017); taking those prices in practice requires accounts at many bookmakers, which limit winning customers. A best price more than 5 probability points longer than the market average is dropped as a stale or erroneous quote (36 of 13,550 prices); this rule was added after finding that many of the 2026-27 maximum over-2.5 prices sat 25–50% above the average.</p>
      <figure style="margin-top:14px">${legend(cNames.map((n, i) => ({ name: n, color: colors[i] })))}${profitChart}<figcaption>Cumulative profit in units, 2% minimum edge, flat stakes.</figcaption></figure>
      <figure style="margin-top:14px">${clvChart}<figcaption>Cumulative closing line value in units for the same bets. A line that rises steadily is the signature of a real edge; profit alone is dominated by noise over a few thousand bets.</figcaption></figure>
      <div class="cols" style="margin-top:18px">
        <div><h3>fair price vs best price, 1X2: result by claimed edge</h3>${buckets("fair vs best price, 1x2")}</div>
        <div><h3>fair price vs best price, over/under 2.5</h3>${buckets("fair vs best price, o/u 2.5")}</div>
      </div></section>

    ${ven ? venuesSection(ven) : ""}

    <section>${secHead("parameter uncertainty", "do wider intervals mean more doubt?")}
      <div class="scroll"><table class="t"><thead><tr><th class="first">quintile of interval width</th><th class="n">matches</th><th class="n">mean sd of P(home)</th><th class="n">mean |model − closing|</th><th class="n">RPS model</th><th class="n">RPS closing</th></tr></thead>
      <tbody>${unc.map((r) => `<tr><td class="first">${r.quintile}</td><td class="n">${r.n}</td><td class="n">${pct(r.mean_sd, 1)}</td><td class="n">${pct(r.mean_abs_gap_close, 1)}</td><td class="n">${num(r.rps_dc, 4)}</td><td class="n">${num(r.rps_close, 4)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">Matches where the model reports more parameter uncertainty tend to be ones where it disagrees more with the closing price. On the test seasons, the closing probability of a home win fell inside the model's 90% interval in about 93% of matches.</p></section>

    ${led ? ledgerSection(led) : ""}`;
}

function venuesSection(v) {
  const res = v.results.filter((r) => r.accuracy);
  const one = res.filter((r) => r.offset_hours === 1);
  const acc = one.map((r) => {
    const a = r.accuracy, dm = r.dm_venue_vs.bookmaker_close;
    return `<tr><td class="first">${VENUE[r.venue]}, 1 h before kick-off</td><td class="n">${r.n_matches}</td><td class="n">${num(a[r.venue].rps, 4)}</td><td class="n">${num(a.bookmaker_close.rps, 4)}</td><td class="n">${num(a.fair.rps, 4)}</td><td class="n">${num(a.model.rps, 4)}</td><td class="n">${num(dm.p_value, 2)}</td></tr>`;
  }).join("");
  const byOut = one.map((r) => (r.buy_every_contract_by_outcome || []).map((b) => `<tr><td class="first">${VENUE[r.venue]}</td><td>${b.selection === "H" ? "home" : b.selection === "D" ? "draw" : "away"}</td><td class="n">${b.n}</td>
    <td class="n">${b.mean_price_minus_close > 0 ? "+" : "−"}${pp(Math.abs(b.mean_price_minus_close), 2)}¢</td>
    <td class="n ${cls(b.clv)}">${signed(b.clv)}</td><td class="n ci">${signed(b.clv_ci90[0])} to ${signed(b.clv_ci90[1])}</td>
    <td class="n ${cls(b.roi)}">${signed(b.roi)}</td><td class="n ci">${signed(b.roi_ci90[0])} to ${signed(b.roi_ci90[1])}</td></tr>`).join("")).join("");
  const strat = one.map((r) => r.strategies.filter((s) => s.forecast !== "bookmaker_close" && s.threshold === 0.02 && s.roi != null).map((s) => `<tr><td class="first">${s.forecast === "fair" ? "fair price" : "model"} vs ${VENUE[r.venue]}</td><td class="n">${s.n_bets}</td><td class="n ${cls(s.roi)}">${signed(s.roi)}</td><td class="n ci">${signed(s.roi_ci90[0])} to ${signed(s.roi_ci90[1])}</td><td class="n ${cls(s.clv)}">${signed(s.clv)}</td></tr>`).join("")).join("");
  return `<section>${secHead("prediction markets", "Kalshi and Polymarket EPL match prices vs the sharp bookmaker close")}
    <div class="scroll"><table class="t"><thead><tr><th class="first">venue</th><th class="n">matches</th><th class="n">RPS venue</th><th class="n">RPS book close</th><th class="n">RPS fair</th><th class="n">RPS model</th><th class="n">p (venue = close)</th></tr></thead><tbody>${acc}</tbody></table></div>
    <p class="note">One hour before kick-off, Kalshi and Polymarket 1X2 prices are as accurate as the de-margined bookmaker close; the differences are far inside the noise.</p>
    <h3 style="margin-top:18px">buying every contract of one outcome, 1 h before kick-off</h3>
    <div class="scroll"><table class="t"><thead><tr><th class="first">venue</th><th>outcome</th><th class="n">contracts</th><th class="n">price − close</th><th class="n">CLV</th><th class="n">90% interval</th><th class="n">ROI</th><th class="n">90% interval</th></tr></thead><tbody>${byOut}</tbody></table></div>
    <p class="note">Costs include fees. Kalshi uses the real ask; Polymarket's history has no bid/ask, so ${esc(v.assumptions.polymarket_cost)}. CLV is negative for every outcome on both venues, roughly the size of the trading costs: there is no systematic mispricing to harvest, and positive ROI on draws is inside the noise.</p>
    <h3 style="margin-top:18px">buying when a forecast says a contract is cheap (edge above 2%)</h3>
    <div class="scroll"><table class="t"><thead><tr><th class="first">strategy</th><th class="n">bets</th><th class="n">ROI</th><th class="n">90% interval</th><th class="n">CLV</th></tr></thead><tbody>${strat}</tbody></table></div>
    <p class="note">The fair price here is built from bookmaker odds collected a day or more earlier, while the venue price one hour before kick-off already reflects later news. Negative CLV says these apparent edges were stale-information artefacts. A live sharp-price feed is needed before prediction-market edges on this board can be trusted; it is the first item on the roadmap.</p>
  </section>`;
}

function ledgerSection(led) {
  const s = led.score;
  const rows = led.rows.slice().reverse().slice(0, 40);
  const src = Object.entries(s.sources || {});
  return `<section>${secHead("live record", "forecasts written to git before kick-off, scored after the result")}
    ${src.length ? `<div class="scroll"><table class="t"><thead><tr><th class="first">source</th><th class="n">settled</th><th class="n">RPS</th><th class="n">log loss</th></tr></thead><tbody>${src.map(([k, v]) => `<tr><td class="first">${esc(k)}</td><td class="n">${v.n}</td><td class="n">${num(v.rps, 4)}</td><td class="n">${num(v.log_loss, 4)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">No forecast in the live record has been settled yet. The first results arrive after gameweek ${rows.length ? rows[rows.length - 1].gameweek : ""} is played.</div>`}
    <div class="scroll" style="margin-top:14px"><table class="t"><thead><tr><th class="first">match</th><th>kick-off</th><th class="n">model H·D·A</th><th class="n">fair H·D·A</th><th class="n">first forecast</th><th class="n">result</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td class="first nowrap">${esc(r.home)} v ${esc(r.away)}</td><td class="nowrap dim">${esc(kick(r.kickoff_utc))}</td>
      <td class="n">${pp(r.model_h, 0)}·${pp(r.model_d, 0)}·${pp(r.model_a, 0)}</td><td class="n">${pp(r.fair_h, 0)}·${pp(r.fair_d, 0)}·${pp(r.fair_a, 0)}</td>
      <td class="n dim">${esc(String(r.first_forecast_utc || "").slice(0, 16).replace("T", " "))}</td>
      <td class="n">${r.result ? `${r.hg}–${r.ag}` : "–"}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Each refresh commits data/ledger/predictions.csv to the repository. A row stops changing at kick-off, so the commit history proves when every forecast was made.</p></section>`;
}

// ---------------------------------------------------------------------------
// method
// ---------------------------------------------------------------------------
async function pageMethod() {
  const [meta, bt] = await Promise.all([load("meta.json"), load("backtest.json").catch(() => null)]);
  const c = meta.model.config;
  const pr = bt?.config?.promoted_prior;
  app.innerHTML = `<div class="prose">
    <div class="kicker">method</div>
    <h1>How the numbers are made</h1>
    <p>Everything on this site is produced by the code in the repository from public data, on a schedule, by GitHub Actions. Nothing is typed in by hand.</p>

    <h2>Data</h2>
    <ul>
      <li><b>Results, match statistics and bookmaker odds</b>: football-data.co.uk, every Premier League match since 2005-06. Pre-match odds are collected on Friday for weekend matches and Tuesday for midweek; closing odds just before kick-off (from 2019-20). Pinnacle odds run from 2012-13 until Pinnacle stopped publishing prices partway through 2025-26; Betfair Exchange odds start in 2024-25.</li>
      <li><b>Expected goals (xG)</b>: Understat, every match since 2014-15.</li>
      <li><b>Fixtures and kick-off times</b>: the Fantasy Premier League API.</li>
      <li><b>Prediction markets</b>: Kalshi (match result, total goals, winning margin, both teams to score, title, relegation) and Polymarket (the same match markets, title, top four, relegation), read from their public APIs.</li>
    </ul>

    <h2>The scoreline model</h2>
    <p>Goals for each side follow a Poisson distribution whose rate depends on the attacking strength of one team and the defensive weakness of the other, plus home advantage (Maher 1982), with the Dixon–Coles correction for the four low scores (Dixon &amp; Coles 1997):</p>
    <div class="eq">log λ_home = μ + home + att[home] + def[away]
log λ_away = μ + att[away] + def[home]
P(x, y) = τ(x, y; ρ) · Poisson(x; λ_home) · Poisson(y; λ_away)</div>
    <p>Three choices were made on validation data and then frozen:</p>
    <ul>
      <li><b>Time decay</b>: each past match is weighted exp(−ξ·age in days), ξ = ${c.xi} (half-life ${Math.round(Math.log(2) / c.xi)} days).</li>
      <li><b>Goals or xG</b>: the model is fitted to ${Math.round(c.w_goals * 100)}% goals + ${Math.round((1 - c.w_goals) * 100)}% xG. xG measures chance quality and is less noisy than goals; fitting to a mix predicted better than either alone.</li>
      <li><b>Prior</b>: every team's strengths have a Gaussian prior with sd ${c.prior_sd}. A newly promoted team's prior is centred on the average first-season strength of promoted teams, ${pr ? `attack ${num(pr.att, 2)}, defence ${num(pr.dfn, 2)}, sd ${num(pr.sd, 2)},` : ""} estimated from 2006-07 to 2015-16 only.</li>
    </ul>
    <p>Settings were chosen by the one-standard-error rule: among all settings whose validation RPS was within one standard error of the best, the most regularised one. Parameter uncertainty is the Laplace approximation (the curvature of the log-posterior at its maximum), scaled by the quasi-Poisson dispersion, because an xG-weighted target varies less than Poisson counts. The intervals shown are the 5th to 95th percentile of each probability over 1,000 parameter draws. They describe uncertainty about team strength, not the randomness of the match.</p>

    <h2>The fair price</h2>
    <p>A bookmaker's 1X2 and over/under 2.5 prices pin down the expected goals of each side. The fair price solves for those market-implied rates and blends them with the model's rates on the log scale, log λ_fair = α·log λ_model + (1 − α)·log λ_market, with α fitted by maximum likelihood on earlier seasons. The current α is ${pct(meta.model.alpha, 1)}: the market dominates, because the backtest shows it is more accurate. Every other market (other goal lines, both teams to score, winning margins, correct score) is then priced from the same scoreline distribution, so all prices on a match are consistent with each other. Before bookmaker odds exist for a match, the fair price is the model alone, and the site says so.</p>
    <p>Bookmaker margins are removed with Shin's method (Shin 1993; Štrumbelj 2014), which was the most accurate of the three standard methods on Pinnacle closing odds; all three were within 0.0001 RPS of each other.</p>

    <h2>Edges and costs</h2>
    <p>For a contract paying $1, edge = fair probability / all-in cost − 1. All-in cost is the ask plus the taker fee: Kalshi charges 0.07·p·(1−p) per contract; Polymarket sports markets charge rate·p·(1−p) with the rate read from each market (0.05 on match markets and 0.03 on season markets at the time of writing). Bookmaker prices are converted with cost = 1 / decimal odds, and Betfair Exchange odds are reduced by a 2% commission on winnings. The Kelly column is a quarter of the full Kelly fraction, a common hedge against error in the probability itself (Baker &amp; McHale 2013).</p>

    <h2>Evaluation protocol</h2>
    <ul>
      <li>Walk-forward: every forecast for day d uses only matches played before day d.</li>
      <li>Promoted-team prior: seasons 2006-07 to 2015-16. Validation (settings chosen): ${esc(bt ? bt.protocol.validation_seasons.join(", ") : "2016-17 to 2018-19")}. Test (settings frozen): 2019-20 onward.</li>
      <li>The model–market blend for test season S is fitted only on forecasts from earlier seasons.</li>
      <li>Metrics: ranked probability score (Epstein 1969; Constantinou &amp; Fenton 2012), log loss, Brier score, calibration. Differences are tested with the Diebold–Mariano test clustered by match day; intervals use a cluster bootstrap over match days.</li>
      <li>Encompassing test: a logarithmic pool of model and market fitted by maximum likelihood. A model weight indistinguishable from zero means the market already contains what the model knows.</li>
      <li>Betting: closing line value (return measured against the de-margined closing price) is reported alongside profit, because profit over a few thousand bets is mostly noise.</li>
    </ul>

    <h2>Changes made after seeing test results</h2>
    <p>The first test run selected the setting with the lowest validation RPS (prior sd 5). Its parameter intervals were unusable for teams with little data, and it gave a promoted team a 3.7% chance of the title. Selection was switched to the one-standard-error rule, the promoted-team prior was given the empirical spread of promoted teams, and the covariance was scaled by the quasi-Poisson dispersion. None of these changes was chosen to improve test RPS; test RPS of the model was 0.2007 before and ${bt ? num(bt.test.models.dc.rps, 4) : "–"} after.</p>

    <h2>Known limitations</h2>
    <ul>
      <li>The model sees only results and xG. It knows nothing about injuries, suspensions, rotation, transfers or managers, all of which the market prices.</li>
      <li>Bookmaker odds arrive twice a week from football-data.co.uk, so the fair price can be a few days old, while Kalshi and Polymarket prices move continuously. This is why edges against prediction markets did not survive to the close in the backtest.</li>
      <li>The season simulation has no market anchor and inherits the model's weaknesses.</li>
      <li>Polymarket's price history has no bid/ask; its historical execution costs are estimated.</li>
    </ul>

    <h2>References</h2>
    <ol class="refs">
      <li>Maher, M. J. (1982). Modelling association football scores. <i>Statistica Neerlandica</i> 36(3).</li>
      <li>Dixon, M. J. &amp; Coles, S. G. (1997). Modelling association football scores and inefficiencies in the football betting market. <i>JRSS C</i> 46(2).</li>
      <li>Epstein, E. S. (1969). A scoring system for probability forecasts of ranked categories. <i>J. Applied Meteorology</i> 8(6).</li>
      <li>Constantinou, A. C. &amp; Fenton, N. E. (2012). Solving the problem of inadequate scoring rules for assessing probabilistic football forecast models. <i>JQAS</i> 8(1).</li>
      <li>Shin, H. S. (1993). Measuring the incidence of insider trading in a market for state-contingent claims. <i>Economic Journal</i> 103.</li>
      <li>Štrumbelj, E. (2014). On determining probability forecasts from betting odds. <i>Int. J. Forecasting</i> 30(4).</li>
      <li>Hvattum, L. M. &amp; Arntzen, H. (2010). Using ELO ratings for match result prediction in association football. <i>Int. J. Forecasting</i> 26(3).</li>
      <li>Diebold, F. X. &amp; Mariano, R. S. (1995). Comparing predictive accuracy. <i>J. Business &amp; Economic Statistics</i> 13(3).</li>
      <li>Fair, R. C. &amp; Shiller, R. J. (1990). Comparing information in forecasts from econometric models. <i>American Economic Review</i> 80(3).</li>
      <li>Kaunitz, L., Zhong, S. &amp; Kreiner, J. (2017). Beating the bookies with their own numbers, and how the online sports betting market is rigged. arXiv:1710.02824.</li>
      <li>Baker, R. D. &amp; McHale, I. G. (2013). Optimal betting under parameter uncertainty: improving the Kelly criterion. <i>Decision Analysis</i> 10(3).</li>
      <li>McCullagh, P. &amp; Nelder, J. A. (1989). <i>Generalized Linear Models</i>, 2nd ed. Chapman &amp; Hall.</li>
      <li>Hastie, T., Tibshirani, R. &amp; Friedman, J. (2009). <i>The Elements of Statistical Learning</i>, 2nd ed., §7.10 (one-standard-error rule).</li>
    </ol>
  </div>`;
}

// ---------------------------------------------------------------------------
// router
// ---------------------------------------------------------------------------
async function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const [name, arg] = h.split("/");
  const r = name || "board";
  setNav(r === "match" ? "board" : r);
  tip.hidden = true;
  try {
    if (r === "match") await pageMatch(decodeURIComponent(arg || ""));
    else if (r === "season") await pageSeason();
    else if (r === "record") await pageRecord();
    else if (r === "method") await pageMethod();
    else await pageBoard();
    bindCharts(app, tip);
  } catch (e) {
    console.error(e);
    app.innerHTML = `<div class="empty">Could not load data (${esc(e.message)}).</div>`;
  }
  if (!location.hash.includes("match")) window.scrollTo(0, 0);
}

async function init() {
  try {
    const meta = await load("meta.json");
    version = meta.generated_utc;
    setStatus(meta);
  } catch (e) {
    document.getElementById("status").textContent = "no data yet";
  }
  window.addEventListener("hashchange", route);
  route();
}

// tooltips for any element with data-tip
document.addEventListener("pointerover", (ev) => {
  const t = ev.target.closest("[data-tip]");
  if (!t) return;
  tip.textContent = t.getAttribute("data-tip");
  tip.hidden = false;
  placeTip(tip, ev.clientX, ev.clientY);
});
document.addEventListener("pointermove", (ev) => {
  if (!tip.hidden && ev.target.closest("[data-tip]")) placeTip(tip, ev.clientX, ev.clientY);
});
document.addEventListener("pointerout", (ev) => { if (ev.target.closest("[data-tip]")) tip.hidden = true; });
document.addEventListener("focusin", (ev) => {
  const t = ev.target.closest("[data-tip]");
  if (!t) return;
  const r = t.getBoundingClientRect();
  tip.textContent = t.getAttribute("data-tip");
  tip.hidden = false;
  placeTip(tip, r.right, r.bottom);
});
document.addEventListener("focusout", () => { tip.hidden = true; });

// row links
document.addEventListener("click", (ev) => {
  if (ev.target.closest("a, button")) return;
  const row = ev.target.closest("tr[data-href]");
  if (row) location.hash = row.dataset.href;
});

// theme toggle: auto -> light -> dark -> auto
document.getElementById("theme").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme || "auto";
  const next = cur === "auto" ? "light" : cur === "light" ? "dark" : "auto";
  if (next === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try { if (next === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", next); } catch (e) {}
  document.getElementById("theme").textContent = next === "auto" ? "theme" : next;
});

init();
