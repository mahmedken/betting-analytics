// Small SVG/HTML chart builders. Every function returns a string.
// Colours come from CSS custom properties so light/dark switch in one place.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const pct = (p, d = 1) => (p == null || !isFinite(p) ? "–" : (100 * p).toFixed(d) + "%");
export const pp = (p, d = 1) => (p == null || !isFinite(p) ? "–" : (100 * p).toFixed(d));
export const signed = (x, d = 1) => (x == null || !isFinite(x) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "±") + Math.abs(100 * x).toFixed(d) + "%");
export const num = (x, d = 2) => (x == null || !isFinite(x) ? "–" : Number(x).toFixed(d));

const niceTicks = (lo, hi, n = 5) => {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
};

// ---------------------------------------------------------------------------
// Edge glyph: the 90% interval of expected return p/cost - 1 against break-even.
// ---------------------------------------------------------------------------
export function edgeGlyph({ lo, p, hi, cost, width = 132 }) {
  if (!(cost > 0) || p == null) return "";
  const R = 0.3;
  const h = 18, pad = 6, w = width;
  const x = (e) => pad + ((Math.max(-R, Math.min(R, e)) + R) / (2 * R)) * (w - 2 * pad);
  const e = p / cost - 1, el = lo / cost - 1, eh = hi / cost - 1;
  const cls = e > 0 ? "var(--pos)" : "var(--neg)";
  const clipL = el < -R ? `<path d="M${pad - 4} ${h / 2} l4 -3 v6z" fill="var(--ink-3)"/>` : "";
  const clipR = eh > R ? `<path d="M${w - pad + 4} ${h / 2} l-4 -3 v6z" fill="var(--ink-3)"/>` : "";
  const tip = `expected return ${signed(e)}\n90% interval ${signed(el)} to ${signed(eh)}\nbreak-even price ${pp(cost, 1)}¢`;
  return `<svg class="glyph" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" data-tip="${esc(tip)}" role="img" aria-label="${esc(tip)}">
    <line x1="${pad}" x2="${w - pad}" y1="${h / 2}" y2="${h / 2}" stroke="var(--rule)" stroke-width="1"/>
    <line x1="${x(0)}" x2="${x(0)}" y1="2" y2="${h - 2}" stroke="var(--ink-2)" stroke-width="1"/>
    <line x1="${x(el)}" x2="${x(eh)}" y1="${h / 2}" y2="${h / 2}" stroke="var(--ink-3)" stroke-width="3" stroke-linecap="round"/>
    <circle cx="${x(e)}" cy="${h / 2}" r="4" fill="${cls}" stroke="var(--paper)" stroke-width="2"/>
    ${clipL}${clipR}
  </svg>`;
}

// ---------------------------------------------------------------------------
// Home / draw / away strip
// ---------------------------------------------------------------------------
export function strip(h, d, a, labels = true) {
  const tip = `home ${pct(h)}\ndraw ${pct(d)}\naway ${pct(a)}`;
  return `<div data-tip="${esc(tip)}"><div class="strip"><i style="width:${100 * h}%"></i><i style="width:${100 * d}%"></i><i style="width:${100 * a}%"></i></div>${
    labels ? `<div class="hda"><span>${pp(h, 0)}</span><span>${pp(d, 0)}</span><span>${pp(a, 0)}</span></div>` : ""}</div>`;
}

// ---------------------------------------------------------------------------
// Sequential shading helper
// ---------------------------------------------------------------------------
const SEQ = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"];
export function seq(v, vmax) {
  if (!(v > 0)) return "transparent";
  const k = Math.min(SEQ.length - 1, Math.floor((v / vmax) * SEQ.length));
  return `var(${SEQ[k]})`;
}

// ---------------------------------------------------------------------------
// Correct-score heatmap
// ---------------------------------------------------------------------------
export function heatmap(M, homeCode, awayCode) {
  const n = M.length;
  let vmax = 0, best = [0, 0];
  M.forEach((r, i) => r.forEach((v, j) => { if (v > vmax) { vmax = v; best = [i, j]; } }));
  let html = `<div class="heat" style="grid-template-columns: 26px repeat(${n}, minmax(0, 1fr))"><div class="h"></div>`;
  for (let j = 0; j < n; j++) html += `<div class="h">${j}</div>`;
  for (let i = 0; i < n; i++) {
    html += `<div class="h">${i}</div>`;
    for (let j = 0; j < n; j++) {
      const v = M[i][j];
      const k = Math.min(SEQ.length - 1, Math.floor((v / vmax) * SEQ.length));
      const dark = k >= 4;
      const show = v >= 0.02 || (i === best[0] && j === best[1]);
      const tip = `${homeCode} ${i} – ${j} ${awayCode}\n${pct(v, 2)}`;
      html += `<div class="c${dark ? " dk" : ""}" style="background:${seq(v, vmax)}" data-tip="${esc(tip)}" tabindex="0">${show ? pp(v, 1) : ""}</div>`;
    }
  }
  return html + "</div>";
}

// ---------------------------------------------------------------------------
// Position distribution (20 cells)
// ---------------------------------------------------------------------------
export function posdist(probs, team) {
  const vmax = Math.max(...probs, 0.01);
  return `<div class="posdist">${probs.map((v, i) => `<i style="background:${seq(v, vmax)}" data-tip="${esc(`${team}: finish ${i + 1}${["st", "nd", "rd"][i] || "th"} ${pct(v)}`)}"></i>`).join("")}</div>`;
}

// ---------------------------------------------------------------------------
// Line chart with crosshair. series: [{name, color, points: [[x, y], ...]}]
// x values are numbers (use Date.getTime() for dates).
// ---------------------------------------------------------------------------
const registry = new Map();
let chartId = 0;

export function lineChart({ series, width = 640, height = 240, xType = "num", yFmt = (v) => v, xFmt, yZero = true, title = "" }) {
  const id = `c${++chartId}`;
  const m = { l: 44, r: 52, t: 10, b: 26 };
  const all = series.flatMap((s) => s.points);
  if (!all.length) return "";
  let x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]));
  let y0 = Math.min(...all.map((p) => p[1])), y1 = Math.max(...all.map((p) => p[1]));
  if (yZero) { y0 = Math.min(0, y0); y1 = Math.max(0, y1); }
  const padY = (y1 - y0) * 0.06 || 1;
  y0 -= padY; y1 += padY;
  const X = (v) => m.l + ((v - x0) / (x1 - x0 || 1)) * (width - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (height - m.t - m.b);
  const fx = xFmt || (xType === "date" ? (v) => new Date(v).toISOString().slice(0, 7) : (v) => v);
  let g = "";
  niceTicks(y0, y1, 4).forEach((t) => {
    g += `<line class="grid" x1="${m.l}" x2="${width - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 3}" text-anchor="end">${yFmt(t)}</text>`;
  });
  if (y0 < 0 && y1 > 0) g += `<line x1="${m.l}" x2="${width - m.r}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--ink-2)" stroke-width="1"/>`;
  const xt = xType === "date"
    ? (() => { const out = []; const a = new Date(x0), b = new Date(x1); for (let y = a.getUTCFullYear(); y <= b.getUTCFullYear(); y++) { const t = Date.UTC(y, 0, 1); if (t >= x0 && t <= x1) out.push(t); } return out.length > 8 ? out.filter((_, i) => i % 2 === 0) : out; })()
    : niceTicks(x0, x1, 5);
  xt.forEach((t) => { g += `<text x="${X(t)}" y="${height - 8}" text-anchor="middle">${xType === "date" ? new Date(t).getUTCFullYear() : fx(t)}</text>`; });
  g += `<line class="axis" x1="${m.l}" x2="${width - m.r}" y1="${height - m.b}" y2="${height - m.b}"/>`;
  let paths = "", labels = "";
  const ends = [];
  series.forEach((s) => {
    if (!s.points.length) return;
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("");
    paths += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = s.points[s.points.length - 1];
    ends.push({ y: Y(last[1]), x: X(last[0]), s, v: last[1] });
  });
  // end labels with simple collision avoidance
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].ly = (ends[i - 1].ly ?? ends[i - 1].y) + 12;
  ends.forEach((e) => {
    const ly = e.ly ?? e.y;
    labels += `<circle cx="${e.x}" cy="${e.y}" r="4" fill="${e.s.color}" stroke="var(--paper)" stroke-width="2"/>`;
    // Identity is carried by the legend above the chart; the end label gives the final value.
    if (ly !== e.y) labels += `<line x1="${e.x + 5}" y1="${e.y}" x2="${e.x + 10}" y2="${ly}" stroke="var(--ink-3)" stroke-width="1"/>`;
    labels += `<text class="lbl" x="${e.x + 12}" y="${ly + 3}">${yFmt(e.v)}</text>`;
  });
  registry.set(id, { series, X, Y, fx, yFmt, m, width, height, x0, x1 });
  return `<svg class="chart" id="${id}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">
    ${g}${paths}${labels}
    <line class="xh" x1="0" x2="0" y1="${m.t}" y2="${height - m.b}" stroke="var(--ink-3)" stroke-width="1" visibility="hidden"/>
    <rect class="hit" data-chart="${id}" x="${m.l}" y="${m.t}" width="${width - m.l - m.r}" height="${height - m.t - m.b}"/>
  </svg>`;
}

export function bindCharts(root, tipEl) {
  root.querySelectorAll("rect.hit[data-chart]").forEach((rect) => {
    const c = registry.get(rect.dataset.chart);
    if (!c) return;
    const svg = rect.ownerSVGElement;
    const xh = svg.querySelector(".xh");
    const move = (ev) => {
      const pt = svg.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
      const xv = c.x0 + ((loc.x - c.m.l) / (c.width - c.m.l - c.m.r)) * (c.x1 - c.x0);
      const lines = [];
      let xs = null;
      c.series.forEach((s) => {
        if (!s.points.length) return;
        let best = s.points[0];
        for (const p of s.points) if (Math.abs(p[0] - xv) < Math.abs(best[0] - xv)) best = p;
        xs = xs ?? best[0];
        lines.push(`${s.label ?? s.name}: ${c.yFmt(best[1])}`);
      });
      if (xs == null) return;
      xh.setAttribute("x1", c.X(xs)); xh.setAttribute("x2", c.X(xs)); xh.setAttribute("visibility", "visible");
      tipEl.textContent = `${c.fx(xs)}\n${lines.join("\n")}`;
      tipEl.hidden = false;
      placeTip(tipEl, ev.clientX, ev.clientY);
    };
    rect.addEventListener("pointermove", move);
    rect.addEventListener("pointerleave", () => { xh.setAttribute("visibility", "hidden"); tipEl.hidden = true; });
  });
}

export function placeTip(tip, x, y) {
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

// ---------------------------------------------------------------------------
// Reliability diagram. series: [{name, color, bins: [{mean_forecast, observed, obs_lo, obs_hi, n}]}]
// ---------------------------------------------------------------------------
export function calibrationChart({ series, width = 360, height = 360, lo = 0, hi = 1, title = "" }) {
  const m = { l: 40, r: 12, t: 10, b: 30 };
  const X = (v) => m.l + ((v - lo) / (hi - lo)) * (width - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (height - m.t - m.b);
  let g = "";
  niceTicks(lo, hi, 5).forEach((t) => {
    g += `<line class="grid" x1="${m.l}" x2="${width - m.r}" y1="${Y(t)}" y2="${Y(t)}"/>`;
    g += `<text x="${m.l - 6}" y="${Y(t) + 3}" text-anchor="end">${Math.round(t * 100)}</text>`;
    g += `<text x="${X(t)}" y="${height - 12}" text-anchor="middle">${Math.round(t * 100)}</text>`;
  });
  g += `<line x1="${X(lo)}" y1="${Y(lo)}" x2="${X(hi)}" y2="${Y(hi)}" stroke="var(--ink-3)" stroke-width="1"/>`;
  g += `<text x="${width - m.r}" y="${height - 1}" text-anchor="end">forecast %</text>`;
  g += `<text x="${m.l}" y="${m.t - 1}" text-anchor="start">observed %</text>`;
  let marks = "";
  series.forEach((s, si) => {
    const bins = s.bins.filter((b) => b.n >= 15);
    const off = (si - (series.length - 1) / 2) * 3;
    marks += `<path d="${bins.map((b, i) => `${i ? "L" : "M"}${X(b.mean_forecast) + off} ${Y(b.observed)}`).join("")}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    bins.forEach((b) => {
      const tip = `${s.name}\nforecast ${pct(b.mean_forecast)} · observed ${pct(b.observed)}\n90% interval ${pct(b.obs_lo)}–${pct(b.obs_hi)} · n=${b.n}`;
      marks += `<line x1="${X(b.mean_forecast) + off}" x2="${X(b.mean_forecast) + off}" y1="${Y(b.obs_lo)}" y2="${Y(b.obs_hi)}" stroke="${s.color}" stroke-width="1"/>`;
      marks += `<circle cx="${X(b.mean_forecast) + off}" cy="${Y(b.observed)}" r="4" fill="${s.color}" stroke="var(--paper)" stroke-width="2" data-tip="${esc(tip)}" tabindex="0"/>`;
    });
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">${g}${marks}</svg>`;
}

// ---------------------------------------------------------------------------
// Horizontal interval plot: rows [{label, v, lo, hi, tip}]
// ---------------------------------------------------------------------------
export function rangeRows({ rows, width = 520, rowH = 20, fmt = (v) => v.toFixed(2), zero = 0, title = "" }) {
  const m = { l: 44, r: 44, t: 18, b: 6 };
  const height = m.t + m.b + rows.length * rowH;
  const lo = Math.min(...rows.map((r) => r.lo), zero), hi = Math.max(...rows.map((r) => r.hi), zero);
  const X = (v) => m.l + ((v - lo) / (hi - lo || 1)) * (width - m.l - m.r);
  let g = "";
  niceTicks(lo, hi, 5).forEach((t) => {
    g += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${m.t - 4}" y2="${height - m.b}"/><text x="${X(t)}" y="${m.t - 7}" text-anchor="middle">${fmt(t)}</text>`;
  });
  g += `<line x1="${X(zero)}" x2="${X(zero)}" y1="${m.t - 4}" y2="${height - m.b}" stroke="var(--ink-2)" stroke-width="1"/>`;
  rows.forEach((r, i) => {
    const y = m.t + i * rowH + rowH / 2;
    g += `<text class="lbl" x="${m.l - 8}" y="${y + 3}" text-anchor="end">${esc(r.label)}</text>`;
    g += `<line x1="${X(r.lo)}" x2="${X(r.hi)}" y1="${y}" y2="${y}" stroke="var(--ink-3)" stroke-width="2" stroke-linecap="round"/>`;
    g += `<circle cx="${X(r.v)}" cy="${y}" r="4" fill="var(--s1)" stroke="var(--paper)" stroke-width="2" data-tip="${esc(r.tip || "")}" tabindex="0"/>`;
    g += `<text x="${width - m.r + 6}" y="${y + 3}">${fmt(r.v)}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">${g}</svg>`;
}

// ---------------------------------------------------------------------------
// Dot chart across categories: series [{name, color, values: [v...]}], cats: [..]
// ---------------------------------------------------------------------------
export function dotChart({ cats, series, width = 640, height = 220, yFmt = (v) => v.toFixed(3), title = "" }) {
  const m = { l: 48, r: 12, t: 10, b: 28 };
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  let y0 = Math.min(...all), y1 = Math.max(...all);
  const pad = (y1 - y0) * 0.15 || 0.01; y0 -= pad; y1 += pad;
  const bw = (width - m.l - m.r) / cats.length;
  const X = (i) => m.l + bw * (i + 0.5);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (height - m.t - m.b);
  let g = "";
  niceTicks(y0, y1, 4).forEach((t) => { g += `<line class="grid" x1="${m.l}" x2="${width - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${m.l - 6}" y="${Y(t) + 3}" text-anchor="end">${yFmt(t)}</text>`; });
  cats.forEach((c, i) => { g += `<text x="${X(i)}" y="${height - 10}" text-anchor="middle">${esc(c)}</text>`; });
  series.forEach((s, si) => {
    const off = (si - (series.length - 1) / 2) * 7;
    const pts = s.values.map((v, i) => (v == null ? null : [X(i) + off, Y(v), v, i])).filter(Boolean);
    g += `<path d="${pts.map((p, k) => `${k ? "L" : "M"}${p[0]} ${p[1]}`).join("")}" fill="none" stroke="${s.color}" stroke-width="1" stroke-opacity="0.5"/>`;
    pts.forEach((p) => { g += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${s.color}" stroke="var(--paper)" stroke-width="2" data-tip="${esc(`${cats[p[3]]}\n${s.name}: ${yFmt(p[2])}`)}" tabindex="0"/>`; });
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}">${g}</svg>`;
}

export function legend(items) {
  return `<div class="legend">${items.map((i) => `<span><i class="${i.dot ? "dot" : ""}" style="background:${i.color}"></i>${esc(i.name)}</span>`).join("")}</div>`;
}
