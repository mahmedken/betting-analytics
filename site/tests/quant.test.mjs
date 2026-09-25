// Checks the browser math against values computed by the Python modules.
// Run: node --test site/tests/*.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { shin, inplay, remaining, dcMatrix, rankGroup, simulateGroup, liveGroupOdds, clockState, cost, bestOfBook, mulberry32 } from "../assets/quant.js";

const close = (a, b, tol = 1e-5) => a.forEach((x, i) => assert.ok(Math.abs(x - b[i]) < tol, `${a} vs ${b}`));

test("Shin margin removal matches models/devig.py", () => {
  close(shin([1.714, 3.4, 5.0]), [0.55337, 0.269395, 0.177235]);
  close(shin([2.1, 3.3, 3.6]), [0.455464, 0.284712, 0.259824]);
});

test("in-play probabilities match models/inplay.py", () => {
  close(inplay(1.4, 1.1, 1, 0, 0.37), [0.780569, 0.179009, 0.040421]);
  close(inplay(0.9, 1.6, 0, 0, 1.0), [0.211542, 0.250298, 0.53816]);
  assert.deepEqual(inplay(1.2, 1.0, 2, 2, 0), [0, 1, 0]);
});

test("goal clock matches models/inplay.py on the published clock", () => {
  const c = JSON.parse(readFileSync(new URL("../data/inplay.json", import.meta.url))).clock;
  const got = [[16.5, 0, false], [45, 1.5, false], [45, 0, true], [59.5, 0, false], [90, 2.5, false]].map(([m, a, h]) => remaining(c, m, a, h));
  close(got, [0.862953, 0.575778, 0.562237, 0.403509, 0.038528]);
  assert.ok(Math.abs(remaining(c, 0) - 1) < 1e-6);
});

test("ESPN clock parsing", () => {
  assert.deepEqual(clockState({ type: { state: "in", name: "STATUS_FIRST_HALF" }, displayClock: "17'" }), { phase: "live", minute: 16.5, added: 0, halftime: false });
  assert.deepEqual(clockState({ type: { state: "in", name: "STATUS_SECOND_HALF" }, displayClock: "90'+4'" }), { phase: "live", minute: 90, added: 3.5, halftime: false });
  assert.equal(clockState({ type: { state: "in", name: "STATUS_HALFTIME" }, displayClock: "45'" }).phase, "ht");
  assert.equal(clockState({ type: { state: "post", name: "STATUS_FULL_TIME" } }).phase, "post");
});

test("Dixon-Coles matrix matches models/dixon_coles.py", () => {
  const { G, cdf } = dcMatrix(1.4, 1.1, -0.05);
  const G1 = G + 1, p = [0, 0, 0];
  for (let k = 0; k < cdf.length; k++) { const v = cdf[k] - (k ? cdf[k - 1] : 0), x = Math.floor(k / G1), y = k % G1; p[x > y ? 0 : x === y ? 1 : 2] += v; }
  close(p, [0.431722, 0.278986, 0.289292]);
});

test("head-to-head ranks above goal difference (same case as tests/test_afcon.py)", () => {
  const res = [["A", "B", 1, 0], ["B", "A", 0, 0], ["A", "C", 0, 1], ["A", "D", 2, 0], ["B", "C", 5, 0], ["B", "D", 5, 0],
    ["C", "D", 0, 0], ["D", "C", 0, 0], ["C", "A", 0, 0], ["D", "A", 2, 1], ["C", "B", 3, 0], ["D", "B", 0, 0]];
  assert.deepEqual(rankGroup(["A", "B", "C", "D"], res, mulberry32(1)), ["C", "A", "B", "D"]);
});

test("group simulation: two qualify, host rule, live score", () => {
  const teams = ["Kenya", "South Africa", "Guinea", "Eritrea"];
  const fx = [];
  for (const h of teams) for (const a of teams) if (h !== a) fx.push({ home: h, away: a, lam: [1.3, 1.2], nu: [1.0, 1.1] });
  const out = simulateGroup({ teams, hosts: new Set(["Kenya"]), fixtures: fx, played: [], live: {}, rho: -0.04, perDraw: 300 });
  const sum = (k) => teams.reduce((s, t) => s + out[t][k], 0);
  assert.ok(Math.abs(sum("p_qualify") - 2) < 1e-9 && Math.abs(sum("p_first") - 1) < 1e-9);
  assert.equal(out.Kenya.p_qualify, 1);
  // South Africa 4-0 up at 85 minutes against Guinea raises its chances
  const live = { "South Africa|Guinea": { hg: 4, ag: 0, rem: 0.05 } };
  const out2 = simulateGroup({ teams, hosts: new Set(["Kenya"]), fixtures: fx, played: [], live, rho: -0.04, perDraw: 300 });
  assert.ok(out2["South Africa"].p_qualify > out["South Africa"].p_qualify + 0.05);
});

test("fees and order books", () => {
  assert.ok(Math.abs(cost(0.5, "kalshi") - 0.5175) < 1e-12);
  assert.ok(Math.abs(cost(0.2, "polymarket", 0.03) - 0.2048) < 1e-12);
  assert.deepEqual(bestOfBook({ bids: [{ price: "0.01", size: "5" }, { price: "0.82", size: "40" }], asks: [{ price: "0.99", size: "1" }, { price: "0.85", size: "12" }] }),
    { bid: 0.82, ask: 0.85, bidSize: 40, askSize: 12 });
});

test("live group odds equal the server's when nothing has changed, and move when a result comes in", () => {
  const teams = ["A", "B", "C", "D"];
  const fx = [];
  for (const h of teams) for (const a of teams) if (h !== a) fx.push({ home: h, away: a, lam: [1.4, 1.3, 1.2], nu: [1.0, 1.1, 1.0] });
  const server = Object.fromEntries(teams.map((t, i) => [t, { p_first: [0.4, 0.3, 0.2, 0.1][i], p_qualify: [0.7, 0.6, 0.4, 0.3][i], positions: [0.25, 0.25, 0.25, 0.25], exp_points: 8 }]));
  const args = { teams, hosts: new Set(), fixtures: fx, rho: -0.04, perDraw: 200, live: {} };
  const same = liveGroupOdds(server, { ...args, played: [] }, []);
  teams.forEach((t) => assert.equal(same[t].p_first, server[t].p_first));
  const won = liveGroupOdds(server, { ...args, played: [["D", "A", 3, 0]] }, []);
  assert.ok(won.D.p_first > server.D.p_first && won.A.p_first < server.A.p_first);
});
