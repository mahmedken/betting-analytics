// Club colours: [primary, secondary]. Text colour on the badge is chosen by luminance.
export const CLUB = {
  "Arsenal": ["#e30613", "#063672"],
  "Aston Villa": ["#670e36", "#95bfe5"],
  "Birmingham": ["#0000ff", "#ffffff"],
  "Blackburn": ["#009ee0", "#ffffff"],
  "Bournemouth": ["#da291c", "#000000"],
  "Brentford": ["#e30613", "#140e0c"],
  "Brighton": ["#0057b8", "#ffcd00"],
  "Burnley": ["#6c1d45", "#99d6ea"],
  "Chelsea": ["#034694", "#dba111"],
  "Coventry": ["#59cbe8", "#0b1f3a"],
  "Crystal Palace": ["#1b458f", "#c4122e"],
  "Everton": ["#003399", "#ffffff"],
  "Fulham": ["#1c1c1c", "#cc0000"],
  "Hull": ["#f5a12d", "#1c1c1c"],
  "Ipswich": ["#3a64a3", "#de2c37"],
  "Leeds": ["#ffcd00", "#1d428a"],
  "Leicester": ["#003090", "#fdbe11"],
  "Liverpool": ["#c8102e", "#00b2a9"],
  "Luton": ["#f78f1e", "#002d62"],
  "Man City": ["#6cabdd", "#1c2c5b"],
  "Man United": ["#da291c", "#fbe122"],
  "Middlesbrough": ["#e11b22", "#ffffff"],
  "Newcastle": ["#241f20", "#41b6e6"],
  "Norwich": ["#00a650", "#fff200"],
  "Nott'm Forest": ["#dd0000", "#ffffff"],
  "Sheffield United": ["#ee2737", "#0d171a"],
  "Southampton": ["#d71920", "#130c0e"],
  "Sunderland": ["#eb172b", "#211e1e"],
  "Tottenham": ["#132257", "#ffffff"],
  "Watford": ["#fbee23", "#ed2127"],
  "West Brom": ["#122f67", "#ffffff"],
  "West Ham": ["#7a263a", "#1bb1e7"],
  "Wolves": ["#fdb913", "#231f20"],
};

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
export const lum = (h) => { const [r, g, b] = hex(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const dist = (a, b) => { const x = hex(a), y = hex(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };

export const color = (team) => (CLUB[team] || ["#6b7280", "#d1d5db"])[0];
export const ink = (bg) => (lum(bg) > 0.4 ? "#0b0d10" : "#ffffff");

// Colours for two national teams from ESPN's single team colour. White and
// near-white would vanish on a light card, so they become a light grey with a
// dark edge; an away colour too close to the home colour becomes a neutral.
export function pairColors(hc = "#6b7280", ac = "#9ca3af") {
  const fix = (c) => (/^#[0-9a-f]{6}$/i.test(c) ? (lum(c) > 0.8 ? "#cfd3d9" : c) : "#6b7280");
  const h = fix(hc);
  let a = fix(ac);
  if (dist(h, a) < 0.35) a = lum(h) > 0.3 ? "#1f2430" : "#9aa1ab";
  return [h, a];
}

// Colours for a fixture: home primary, and away primary unless it is too close
// to the home colour, in which case the away secondary.
export function pair(home, away) {
  const h = CLUB[home] || ["#6b7280", "#d1d5db"];
  const a = CLUB[away] || ["#9ca3af", "#374151"];
  let ac = a[0];
  if (dist(h[0], a[0]) < 0.35) ac = dist(h[0], a[1]) > 0.35 && a[1] !== "#ffffff" ? a[1] : "#0b0d10";
  return [h[0], ac];
}
