// TEST ONLY. Usage: node tests/test_chain.js <path-to-a-web/data-directory-built-from-test-fixtures>
const fs = require('fs');
const path = require('path');
const C = require('../web/chain.js');
const dir = process.argv[2];
if (!dir) { console.error('give the data dir'); process.exit(2); }
const rd = p => JSON.parse(fs.readFileSync(path.join(dir, p), 'utf8'));
const net = rd('network/reaches.json'), anchors = rd('anchors.json'), expo = rd('exposure/reach_exposure.json'), lakes = rd('glacial_lakes.geojson');
const g = C.makeGraph(net);
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.error('FAIL:', m); } else console.log('ok  :', m); };

// 1. trace from the closely linked lake
const close = lakes.features.find(f => f.properties.name === 'Closelake').properties;
ok(close.reach !== null && close.reach >= 0, 'close lake has a reach link');
const full = C.tracePath(g, close.reach, null);
ok(full.idx.length >= 5, `full path has several reaches (${full.idx.length})`);
ok(full.reason === 'leaves_selection' || full.reason === 'terminal', 'path ends with a recorded reason: ' + full.reason);
const h = C.tracePath(g, close.reach, 60);
ok(h.reason === 'horizon' && h.idx.length < full.idx.length, 'horizon cuts the path');
// path never revisits a reach
ok(new Set(full.idx).size === full.idx.length, 'no repeated reaches');

// 2. many starts are de-duplicated
const many = C.traceMany(g, [close.reach, close.reach, full.idx[2]], null);
ok(many.idx.length === full.idx.length, 'union of overlapping starts is de-duplicated');

// 3. summary lists districts in order and names rivers
const s = C.summarise(g, full);
ok(s.districts.length >= 1 && s.rivers.includes('Testganga'), `summary: ${s.districts.map(d => d.n).join('>')}; rivers ${s.rivers}`);

// 4. exposure: totals + null stays null (never zero)
const ex = C.sumExposure(expo, full.idx);
const pop = ex.find(e => e.key === 'population');
ok(pop && pop.total > 0 && pop.covered === full.idx.length, 'population summed over all reaches');
const crop = ex.find(e => e.key === 'cropland_ha');
ok(crop.covered < crop.of, `cropland covers only ${crop.covered}/${crop.of} reaches (tile limited)`);
const allNull = C.sumExposure({ layers: { x: { values: [null, null] } } }, [0, 1]);
ok(allNull[0].total === null, 'a layer with no values returns null, not 0');

// 5. district entry reaches
const entry = C.entryReachesForDistricts(g, anchors, ['D0', 'D1'], 40);
ok(entry.length > 0, 'district entry reaches found');

// 6. freshness + severity
ok(C.freshness(new Date().toISOString(), 48) === 'CURRENT', 'fresh timestamp is CURRENT');
ok(C.freshness(new Date(Date.now() - 5 * 864e5).toISOString(), 48) === 'STALE', 'old timestamp is STALE');
ok(C.freshness(null, 48) === 'UNKNOWN', 'missing timestamp is UNKNOWN');
ok(C.severityRank('Extreme') === 4 && C.severityRank('weird') === 0, 'severity ranking');

// 7. loop safety
const loop = C.makeGraph({ ids: [1, 2], next: [1, 0], end: [0, 0], len_km: [1, 1], ord: [1, 1], dis_cms: [1, 1], district: [-1, -1], name: [-1, -1], coords: [[], []] });
const lp = C.tracePath(loop, 0, null);
ok(lp.reason === 'loop' && lp.idx.length === 2, 'cycle detected without hanging');
console.log(fails ? `${fails} FAILED` : 'ALL CHAIN TESTS PASSED');
process.exit(fails ? 1 : 0);
