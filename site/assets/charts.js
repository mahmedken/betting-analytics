// Chart and small-component builders. All return HTML/SVG strings.
import { pair, color, ink } from "./teams.js";

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const pct = (p, d = 0) => (p == null || !isFinite(p) ? "–" : `${(100 * p).toFixed(d)}%`);
export const signed = (x, d = 1) => (x == null || !isFinite(x) ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(100 * x).toFixed(d)}%`);
export const cents = (p, d = 0) => (p == null || !isFinite(p) ? "–" : `${(100 * p).toFixed(d)}¢`);
export const num = (x, d = 2) => (x == null || !isFinite(x) ? "–" : Number(x).toFixed(d));

export function badge(team, code, size = "") {
  const c = color(team);
  return `<span class="badge ${size}" style="background:${c};color:${ink(c)}">${esc(code)}</span>`;
}

// Home / draw / away bar in club colours
export function bar3(home, away, h, d, a) {
  const [hc, ac] = pair(home, away);
  return `<div class="bar3" data-tip="${esc(`home ${pct(h, 1)} · draw ${pct(d, 1)} · away ${pct(a, 1)}`)}">
    <i style="width:${100 * h}%;background:${hc}"></i><i class="dr" style="width:${100 * d}%"></i><i style="width:${100 * a}%;background:${ac}"></i></div>`;
}

// Horizontal bars with 90% interval whiskers; rows [{label, clv, clv_ci90, n_filled, with_bias}]
export function barsCI(rows, { unit = "pct", width = 560 } = {}) {
  const rowH = 40, m = { l: 210, r: 70, t: 10, b: 24 };
  const vals = rows.flatMap((r) => [r.clv, ...(r.clv_ci90 || [])]).filter((v) => v != null);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const H = m.t + m.b + rows.length * rowH;
  const X = (v) => m.l + ((v - lo) / (hi - lo)) * (width - m.l - m.r);
  let g = `<line class="zero" x1="${X(0)}" x2="${X(0)}" y1="${m.t - 4}" y2="${H - m.b + 4}"/>`;
  rows.forEach((r, i) => {
    const y = m.t + i * rowH + 8, h = 18;
    const c = r.clv >= 0 ? "var(--blue)" : "var(--cold)";
    const x0 = X(Math.min(0, r.clv)), x1 = X(Math.max(0, r.clv));
    g += `<text x="0" y="${y + 13}" style="fill:var(--ink);font-size:13px">${esc(r.label)}</text>`;
    g += `<rect x="${x0}" y="${y}" width="${Math.max(1, x1 - x0)}" height="${h}" rx="4" fill="${c}" opacity="${r.with_bias === false ? 0.45 : 1}"/>`;
    if (r.clv_ci90) g += `<line x1="${X(r.clv_ci90[0])}" x2="${X(r.clv_ci90[1])}" y1="${y + h / 2}" y2="${y + h / 2}" stroke="var(--ink)" stroke-width="1.5"/>`;
    g += `<text x="${width - m.r + 8}" y="${y + 13}" style="fill:var(--ink);font-weight:600;font-size:13px">${signed(r.clv)}</text>`;
    g += `<rect x="0" y="${y - 6}" width="${width}" height="${rowH - 4}" fill="transparent" data-tip="${esc(`${r.label}\n${signed(r.clv)} (90% interval ${signed(r.clv_ci90?.[0])} to ${signed(r.clv_ci90?.[1])})\n${r.n_filled ?? ""} orders`)}"/>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${H}">${g}</svg>`;
}

// Diverging bars per team; rows [{team, gap_cents}]
export function teamBars(rows, { width = 560 } = {}) {
  const rowH = 22, m = { l: 120, r: 50, t: 6, b: 6 };
  const lim = Math.max(...rows.map((r) => Math.abs(r.gap_cents)), 1);
  const H = m.t + m.b + rows.length * rowH;
  const X = (v) => m.l + ((v + lim) / (2 * lim)) * (width - m.l - m.r);
  let g = `<line class="zero" x1="${X(0)}" x2="${X(0)}" y1="0" y2="${H}"/>`;
  rows.forEach((r, i) => {
    const y = m.t + i * rowH;
    const c = r.team === "Draw" ? "var(--draw)" : color(r.team);
    g += `<text x="${m.l - 8}" y="${y + 14}" text-anchor="end" style="fill:var(--ink-2);font-size:12px">${esc(r.team)}</text>`;
    g += `<rect x="${Math.min(X(0), X(r.gap_cents))}" y="${y + 3}" width="${Math.abs(X(r.gap_cents) - X(0))}" height="14" rx="3" fill="${c}" stroke="var(--ink-3)" stroke-opacity="0.35"/>`;
    g += `<text x="${width - m.r + 6}" y="${y + 14}" style="font-size:12px;fill:var(--ink)">${r.gap_cents > 0 ? "+" : "−"}${Math.abs(r.gap_cents).toFixed(1)}¢</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${H}">${g}</svg>`;
}

// Small vertical bar pairs; rows [{label, a, b}], names [a, b]
export function pairBars(rows, names, { width = 360, height = 170, fmt = (v) => v.toFixed(3) } = {}) {
  const m = { l: 6, r: 6, t: 20, b: 24 };
  const max = Math.max(...rows.flatMap((r) => [r.a, r.b])) * 1.15;
  const gw = (width - m.l - m.r) / rows.length, bw = Math.min(34, gw / 3);
  const Y = (v) => m.t + (1 - v / max) * (height - m.t - m.b);
  let g = "";
  rows.forEach((r, i) => {
    const cx = m.l + gw * (i + 0.5);
    [[r.a, "var(--blue)", -1], [r.b, "var(--draw)", 1]].forEach(([v, c, s]) => {
      const x = cx + (s < 0 ? -bw - 2 : 2);
      g += `<rect x="${x}" y="${Y(v)}" width="${bw}" height="${Y(0) - Y(v)}" rx="4" fill="${c}"/>`;
      g += `<text x="${x + bw / 2}" y="${Y(v) - 5}" text-anchor="middle" style="fill:var(--ink);font-size:11px">${fmt(v)}</text>`;
    });
    g += `<text x="${cx}" y="${height - 6}" text-anchor="middle" style="fill:var(--ink-2);font-size:12px">${esc(r.label)}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}">${g}</svg>`;
}

// Dot row on a zoomed axis; rows [{label, v, hl}]
export function dotRow(rows, { width = 560, fmt = (v) => v.toFixed(4) } = {}) {
  const m = { l: 130, r: 64, t: 8, b: 8 }, rowH = 30;
  const vs = rows.map((r) => r.v);
  const lo = Math.min(...vs), hi = Math.max(...vs), pad = (hi - lo) * 0.25 || 0.001;
  const X = (v) => m.l + ((v - lo + pad) / (hi - lo + 2 * pad)) * (width - m.l - m.r);
  const H = m.t + m.b + rows.length * rowH;
  let g = "";
  rows.forEach((r, i) => {
    const y = m.t + i * rowH + 15;
    g += `<text x="0" y="${y + 4}" style="fill:var(--ink-2);font-size:13px">${esc(r.label)}</text>`;
    g += `<line x1="${m.l}" x2="${width - m.r}" y1="${y}" y2="${y}" class="grid"/>`;
    g += `<circle cx="${X(r.v)}" cy="${y}" r="7" fill="${r.hl ? "var(--blue)" : "var(--ink-3)"}"/>`;
    g += `<text x="${width - m.r + 8}" y="${y + 4}" style="fill:var(--ink);font-size:13px;font-weight:600">${fmt(r.v)}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${H}">${g}</svg>`;
}

// Sequential shading for heatmaps and position strips
const RAMP_L = ["#eef2ff", "#d6e0ff", "#b3c5ff", "#8aa5ff", "#5f82ff", "#3a5ff2", "#2340c9"];
const RAMP_D = ["#171c2b", "#1d2750", "#223377", "#2a44a5", "#3a5ff2", "#6c8bff", "#a9bcff"];
export function shade(v, vmax) {
  if (!(v > 0)) return "var(--card-2)";
  const dark = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const r = dark ? RAMP_D : RAMP_L;
  return r[Math.min(r.length - 1, Math.floor((v / vmax) * r.length))];
}

export function heatmap(M, hc, ac) {
  const n = M.length;
  const vmax = Math.max(...M.flat());
  let html = `<div class="heat" style="grid-template-columns:22px repeat(${n},minmax(0,1fr))"><div class="h"></div>`;
  for (let j = 0; j < n; j++) html += `<div class="h">${j}</div>`;
  for (let i = 0; i < n; i++) {
    html += `<div class="h">${i}</div>`;
    for (let j = 0; j < n; j++) {
      const v = M[i][j], k = v / vmax;
      html += `<div class="c" style="background:${shade(v, vmax)};color:${k > 0.55 ? "#fff" : "var(--ink-2)"}" data-tip="${esc(`${hc} ${i}–${j} ${ac}: ${pct(v, 1)}`)}">${v >= 0.03 ? (100 * v).toFixed(0) : ""}</div>`;
    }
  }
  return html + "</div>";
}

export function posStrip(probs, team) {
  const vmax = Math.max(...probs, 0.01);
  return `<div class="pd">${probs.map((v, i) => `<i style="background:${shade(v, vmax)}" data-tip="${esc(`${team}: ${i + 1}${["st", "nd", "rd"][i] || "th"} ${pct(v, 1)}`)}"></i>`).join("")}</div>`;
}

// Line chart for cumulative series; series [{name, color, points:[[t, y]]}]
export function lines(series, { width = 720, height = 240, yFmt = (v) => v.toFixed(0) } = {}) {
  const m = { l: 40, r: 50, t: 10, b: 24 };
  const all = series.flatMap((s) => s.points);
  const x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]));
  let y0 = Math.min(0, ...all.map((p) => p[1])), y1 = Math.max(0, ...all.map((p) => p[1]));
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const X = (v) => m.l + ((v - x0) / (x1 - x0 || 1)) * (width - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (height - m.t - m.b);
  let g = `<line class="zero" x1="${m.l}" x2="${width - m.r}" y1="${Y(0)}" y2="${Y(0)}"/>`;
  const step = Math.pow(10, Math.floor(Math.log10((y1 - y0) / 3 || 1)));
  for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) {
    if (Math.abs(v) < 1e-9) continue;
    g += `<line class="grid" x1="${m.l}" x2="${width - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 6}" y="${Y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`;
  }
  const yrs = new Set(all.map((p) => new Date(p[0]).getUTCFullYear()));
  [...yrs].forEach((yr) => { const t = Date.UTC(yr, 6, 1); if (t > x0 && t < x1) g += `<text x="${X(t)}" y="${height - 6}" text-anchor="middle">${yr}</text>`; });
  series.forEach((s) => {
    g += `<path d="${s.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("")}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round"/>`;
    const last = s.points[s.points.length - 1];
    g += `<text x="${X(last[0]) + 6}" y="${Y(last[1]) + 4}" style="fill:var(--ink);font-weight:600">${yFmt(last[1])}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}">${g}</svg>`;
}

export const legend = (items) => `<div class="legend">${items.map((i) => `<span><i style="background:${i.color}"></i>${esc(i.name)}</span>`).join("")}</div>`;

export function placeTip(tip, x, y) {
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = y - r.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}
