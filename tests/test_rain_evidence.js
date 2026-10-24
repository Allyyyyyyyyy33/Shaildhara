// TEST ONLY. Fictional shapes and stations, never real data. Usage: node tests/test_rain_evidence.js
const C = require('../web/chain.js');
let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.error('FAIL:', m); } else console.log('ok  :', m); };
const square = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
const poly = { type: 'Polygon', coordinates: [square(0, 0, 2, 2), square(0.8, 0.8, 1.2, 1.2)] };     // 2x2 degree square with a hole
const multi = { type: 'MultiPolygon', coordinates: [[square(10, 10, 11, 11)], [square(20, 20, 21, 21)]] };
ok(C.geometryContains(poly, 0.3, 0.3), 'point inside polygon is inside');
ok(!C.geometryContains(poly, 1.0, 1.0), 'point inside a hole is NOT inside');
ok(!C.geometryContains(poly, 3, 3), 'point outside is outside');
ok(C.geometryContains(multi, 20.5, 20.5) && !C.geometryContains(multi, 15, 15), 'multipolygon: second part counts, gap does not');
ok(!C.geometryContains(null, 1, 1) && !C.geometryContains({ type: 'Point', coordinates: [1, 1] }, 1, 1), 'missing / non-area geometry is never "inside"');
const mk = (station, lon, lat, parameter, value, observed_at) => ({ station, lon, lat, parameter, value, observed_at });
const obs = [
  mk('Inside-old', 0.3, 0.3, 'Telemetry Hourly Rainfall (mm)', 2, '2026-10-20T06:00:00+00:00'),
  mk('Inside-new', 0.5, 0.4, 'Telemetry Hourly Rainfall (mm)', 5, '2026-10-22T06:00:00+00:00'),
  mk('Near', 2.3, 1.0, 'Telemetry Hourly Rainfall (mm)', 1, '2026-10-22T06:00:00+00:00'),
  mk('Far', 8, 8, 'Telemetry Hourly Rainfall (mm)', 1, '2026-10-22T06:00:00+00:00'),
  mk('River-not-rain', 0.3, 0.3, 'Manual Daily River Water Discharge (m3/sec)', 100, '2026-10-22T06:00:00+00:00'),
  mk('Bad-value', 0.4, 0.4, 'Telemetry Hourly Rainfall (mm)', NaN, '2026-10-22T06:00:00+00:00'),
];
const ev = C.rainfallEvidence(obs, [poly], 100);
ok(ev.inside.map(s => s.station).join() === 'Inside-new,Inside-old', 'inside stations listed newest first: ' + ev.inside.map(s => s.station));
ok(ev.near.length === 1 && ev.near[0].station === 'Near' && ev.near[0].distKm > 20 && ev.near[0].distKm < 40, 'near station found with a plausible distance: ' + (ev.near[0] && ev.near[0].distKm.toFixed(1)));
ok(!ev.inside.concat(ev.near).some(s => s.station === 'Far'), 'station beyond the limit is not reported');
ok(!ev.inside.concat(ev.near).some(s => s.station === 'River-not-rain'), 'river discharge readings are never treated as rainfall');
ok(!ev.inside.concat(ev.near).some(s => s.station === 'Bad-value'), 'reading without a finite value is dropped');
ok(ev.rainStationsTotal === 4, 'total counts only valid rainfall stations: ' + ev.rainStationsTotal);
const none = C.rainfallEvidence([], [poly], 100);
ok(none.inside.length === 0 && none.near.length === 0 && none.rainStationsTotal === 0, 'no stations -> empty result, no crash');
ok(/impossible/.test(C.obsFlag(mk('x', 0, 0, 'Telemetry Hourly Rainfall (mm)', -18.7, '2026-10-21T00:00:00+00:00')) || ''), 'negative rainfall is flagged as a data error');
ok(C.obsFlag(mk('x', 0, 0, 'Telemetry Hourly Rainfall (mm)', 0, '2026-10-21T00:00:00+00:00')) === null, 'zero rainfall is a valid reading, not flagged');
ok(C.obsFlag(mk('x', 0, 0, 'River Water Level Telemetry Hourly (meter)', -1, '2026-10-21T00:00:00+00:00')) === null, 'the negative-rain rule never touches other parameters');
if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('RAIN EVIDENCE TEST PASSED');
