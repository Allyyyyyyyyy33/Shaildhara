// Smoke test on the COMMITTED web/data (real data). Usage: node tests/test_real_data.js [web/data dir]
const fs = require('fs'), path = require('path');
const C = require('../web/chain.js');
const dir = process.argv[2] || path.join(__dirname, '..', 'web', 'data');
const rd = p => JSON.parse(fs.readFileSync(path.join(dir, p), 'utf8'));
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.error('FAIL:', m); } else console.log('ok  :', m); };

const net = rd('network/reaches.json'), anchors = rd('anchors.json'), expo = rd('exposure/reach_exposure.json');
const lakes = rd('glacial_lakes.geojson'), alerts = rd('live/alerts.json');
const g = C.makeGraph(net);
ok(g.n > 1000, `graph loaded: ${g.n} exported reaches`);
let bad = 0; for (let i = 0; i < g.n; i++) if (g.next[i] >= g.n) bad++;
ok(bad === 0, 'every downstream link points at an exported reach');

// every lake with a link traces to a path with a recorded end reason
const linked = lakes.features.filter(f => f.properties.reach != null && f.properties.reach >= 0);
ok(linked.length > 100, `${linked.length} of ${lakes.features.length} glacial lakes have a reach link`);
const reasons = {}; let empty = 0, loops = 0;
for (const f of linked) {
  const t = C.tracePath(g, f.properties.reach, null);
  reasons[t.reason] = (reasons[t.reason] || 0) + 1;
  if (!t.idx.length) empty++;
  if (t.reason === 'loop') loops++;
}
console.log('     path end reasons:', JSON.stringify(reasons));
ok(empty === 0, 'no lake produces an empty path');
ok(!reasons.unknown, 'every path ends with a recorded reason (none unknown)');

// one concrete, verifiable demonstration: the lake whose path crosses the most districts
let best = null;
for (const f of linked) {
  const t = C.tracePath(g, f.properties.reach, null), s = C.summarise(g, t);
  if (!best || s.districts.length > best.s.districts.length) best = { f, t, s };
}
console.log('     longest-chain lake:', best.f.properties.name, '|', Math.round(best.t.totalKm), 'km |', best.s.districts.length, 'districts |', best.s.states.length, 'states | rivers:', best.s.rivers.slice(0, 6).join(', '));
const ex = C.sumExposure(expo, best.t.idx);
console.log('     exposure layers:', ex.map(e => `${e.key}=${e.total == null ? 'NO DATA' : Math.round(e.total)}`).join(', '));
ok(best.s.districts.length >= 2, 'a real chain crosses multiple districts');

// alerts: every one links to its official source, has an issue time with an explicit offset, and the expiry rule works
const A = alerts.alerts;
ok(A.every(a => /^https:\/\/sachet\.ndma\.gov\.in\//.test(a.cap_url || '')), 'every alert carries an official SACHET source link');
ok(A.every(a => /[+-]\d\d:\d\d$|Z$/.test(a.sent || '')), 'every alert issue time carries an explicit UTC offset');
const t0 = Date.parse(A[0].expires);
ok(C.alertActive(A[0], t0 - 1000) && !C.alertActive(A[0], t0 + 1000), 'alert is current before its expiry and not after');
ok(C.alertActive({ sent: new Date(Date.now() - 3600e3).toISOString() }) && !C.alertActive({ sent: new Date(Date.now() - 50 * 3600e3).toISOString() }), 'no-expiry alerts: current for 48 h only');
const matched = A.filter(a => a.districts.length);
console.log(`     alerts in file: ${A.length}, with matched districts: ${matched.length}, active now: ${A.filter(a => C.alertActive(a)).length}`);
if (matched.length) {
  const a = matched[0];
  const starts = C.entryReachesForDistricts(g, anchors, a.districts, 12);
  const t = C.traceMany(g, starts, null);
  ok(t.idx.length > 0, `alert chain from "${a.event}" (${a.districts.length} district(s)) traces ${t.idx.length} reaches`);
}
if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
console.log('REAL-DATA SMOKE TEST PASSED');
