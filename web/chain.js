/* SHAILDHARA chain engine - pure functions, no DOM. Works in the browser (window.Chain) and in Node (tests).
 *
 * Everything here is a SPATIAL connection computed from dataset topology. Nothing is a forecast and nothing
 * estimates damage. Missing data stays missing (null) and is never turned into zero.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Chain = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ---- evidence labels (the vocabulary used on every card) ----
  const LABELS = {
    OFFICIAL: { text: 'Official', cls: 'ev-official', tip: 'Issued by a government agency. SHAILDHARA shows it as issued and adds nothing.' },
    CURRENT: { text: 'Current', cls: 'ev-current', tip: 'The data date is within the freshness window for this source.' },
    LATEST: { text: 'Latest official update', cls: 'ev-latest', tip: 'Newest official version available, not a live feed. Check the date shown.' },
    COMPUTED: { text: 'Computed spatial connection', cls: 'ev-computed', tip: 'Calculated by SHAILDHARA from map geometry. A geographic link, not a physical prediction.' },
    SCENARIO: { text: 'Scenario', cls: 'ev-scenario', tip: 'A user-chosen or illustrative setting. Not a forecast.' },
    NODATA: { text: 'No current data', cls: 'ev-nodata', tip: 'No reliable current information is available for this item.' },
  };

  function makeGraph(net) {
    return {
      n: net.ids.length, ids: net.ids, next: net.next, end: net.end, len: net.len_km, ord: net.ord, dis: net.dis_cms, acc: net.acc || null,
      district: net.district, name: net.name, names: net.names || [], districts: net.districts || [], coords: net.coords,
    };
  }

  // Follow NEXT_DOWN links from `start`. Stops at the sea/terminal, where the network leaves the India
  // selection, at a loop, or when the horizon (km, optional) is reached.
  function tracePath(g, start, maxKm) {
    const idx = [], cum = [];
    const seen = new Set();
    let cur = start, total = 0, reason = 'unknown';
    while (cur >= 0) {
      if (seen.has(cur)) { reason = 'loop'; break; }
      if (maxKm != null && total >= maxKm) { reason = 'horizon'; break; }
      idx.push(cur); seen.add(cur);
      total += g.len[cur] || 0;
      cum.push(total);
      const nx = g.next[cur];
      if (nx < 0) { reason = g.end[cur] === 1 ? 'terminal' : (g.end[cur] === 2 ? 'leaves_selection' : 'unknown'); break; }
      cur = nx;
    }
    return { idx, cum, totalKm: total, reason };
  }

  // Union of several starting reaches, de-duplicated, ordered by distance from the nearest start.
  function traceMany(g, starts, maxKm) {
    const dist = new Map();
    const reasons = {};
    const per = [];
    for (const s of starts) {
      if (s == null || s < 0) continue;
      const t = tracePath(g, s, maxKm);
      per.push({ start: s, ...t });
      reasons[t.reason] = (reasons[t.reason] || 0) + 1;
      t.idx.forEach((r, i) => {
        const d = t.cum[i];
        if (!dist.has(r) || d < dist.get(r)) dist.set(r, d);
      });
    }
    const idx = Array.from(dist.keys()).sort((a, b) => dist.get(a) - dist.get(b));
    let km = 0;
    idx.forEach(r => { km += g.len[r] || 0; });
    return { idx, dist, per, reasons, totalKm: km };
  }

  // Districts / states / rivers along the traced reaches, in order of first appearance.
  function summarise(g, traced) {
    const dOrder = [], dSeen = new Set(), sOrder = [], sSeen = new Set(), rivers = [], rSeen = new Set();
    let unmapped = 0;
    traced.idx.forEach(r => {
      const di = g.district[r];
      if (di >= 0 && g.districts[di]) {
        const d = g.districts[di];
        if (!dSeen.has(d.id)) { dSeen.add(d.id); dOrder.push(d); }
        const sk = d.s || d.sid;
        if (sk && !sSeen.has(sk)) { sSeen.add(sk); sOrder.push(sk); }
      } else unmapped++;
      const ni = g.name[r];
      if (ni >= 0 && g.names[ni] && !/^\d+(\.\d+)?$/.test(String(g.names[ni]).trim()) && !rSeen.has(g.names[ni])) { rSeen.add(g.names[ni]); rivers.push(g.names[ni]); }   // numeric values are codes, not names
    });
    return { districts: dOrder, states: sOrder, rivers, unmappedReaches: unmapped, reaches: traced.idx.length, km: traced.totalKm };
  }

  // Sum each exposure layer over the traced reaches. null values are skipped and reported, never counted as 0.
  function sumExposure(expo, idxs) {
    const out = [];
    if (!expo || !expo.layers) return out;
    for (const key of Object.keys(expo.layers)) {
      const L = expo.layers[key];
      let total = 0, covered = 0;
      for (const r of idxs) {
        const v = L.values[r];
        if (v !== null && v !== undefined) { total += v; covered++; }
      }
      out.push({
        key, label: L.label, unit: L.unit, dataset: L.dataset, method: L.method, caveats: L.caveats,
        total: covered ? total : null, covered, of: idxs.length,
      });
    }
    return out;
  }

  function freshness(iso, maxHours) {
    if (!iso) return 'UNKNOWN';
    const t = Date.parse(iso);
    if (isNaN(t)) return 'UNKNOWN';
    return (Date.now() - t) / 3.6e6 <= maxHours ? 'CURRENT' : 'STALE';
  }

  // An alert is current only while its own expiry time has not passed (or, with no expiry, for 48 h after it was sent).
  function alertActive(a, nowMs) {
    const now = nowMs == null ? Date.now() : nowMs;
    const exp = Date.parse(a && a.expires);
    if (a && a.expires && !isNaN(exp)) return exp >= now;
    const sent = Date.parse(a && a.sent);
    return !isNaN(sent) && (now - sent) / 3.6e6 <= 48;
  }

  const SEV = { extreme: 4, severe: 3, moderate: 2, minor: 1 };
  function severityRank(s) { return SEV[String(s || '').toLowerCase()] || 0; }

  // Entry reaches for a set of districts: the largest reaches (modelled discharge) first, capped.
  function entryReachesForDistricts(g, anchors, dids, cap) {
    const set = new Set();
    for (const d of dids || []) (anchors.districts[d] || []).forEach(r => set.add(r));
    const rank = r => (g.dis[r] || 0) || (g.acc ? (g.acc[r] || 0) : 0);
    const arr = Array.from(set).sort((a, b) => rank(b) - rank(a));
    return arr.slice(0, cap == null ? 40 : cap);
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371, rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  // Stations (with lat/lon) lying within `km` of any traced reach vertex.
  function stationsNearPath(g, idxs, stations, km) {
    const out = [];
    for (const s of stations || []) {
      let best = Infinity;
      for (const r of idxs) {
        const c = g.coords[r];
        for (let i = 0; i < c.length; i += 1) {
          const d = haversineKm(s.lat, s.lon, c[i][1], c[i][0]);
          if (d < best) best = d;
          if (best <= km) break;
        }
        if (best <= km) break;
      }
      if (best <= km) out.push({ ...s, distKm: best });
    }
    return out;
  }

  return { LABELS, makeGraph, tracePath, traceMany, summarise, sumExposure, freshness, alertActive, severityRank, entryReachesForDistricts, haversineKm, stationsNearPath };
});
