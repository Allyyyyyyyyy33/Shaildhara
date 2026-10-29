/* SHAILDHARA web application.
 * Reads only the files produced by the data pipeline (web/data/...). If a file is missing, the interface says so;
 * it never fills gaps with sample or invented values. All text coming from data files is escaped before display. */
(function () {
  'use strict';
  const C = window.Chain;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = u => (/^https?:\/\//i.test(u || '') ? u : '');
  const MATCH_LABEL = { POLYGON: 'by alert polygon', NAME_MATCH: 'by area name, lower confidence',
    NAME_AND_FUZZY_MATCH: 'by area name, plus spelling variants of district names within the alert\'s state, lower confidence - please check',
    FUZZY_NAME_MATCH: 'by approximate spelling of the district name within the alert\'s state, lower confidence - please check',
    TEXT_NAME_MATCH: 'by district names found in the alert text within its state, lower confidence - please check' };
  const SEV_COLOR = { 4: '#b71c1c', 3: '#e65100', 2: '#f9a825', 1: '#fdd835' };
  const D = {};                 // loaded data
  let G = null;                 // river graph
  let map, layers = {}, chainGroup, lakeLayer, districtLayer, stateLayer, obsLayer, base;
  let districtById = {}, lakeById = {}, sevByDistrict = {}, alertsByDistrict = {};
  let current = null;           // current signal
  let anim = { token: 0 };
  let horizonKm = 100;

  const fmt = iso => {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) + ' IST';
  };
  const ago = iso => {
    const t = Date.parse(iso); if (!iso || isNaN(t)) return '';
    const h = (Date.now() - t) / 3.6e6;
    if (h < 0) return 'just now';
    if (h < 1) return Math.max(1, Math.round(h * 60)) + ' min ago';
    if (h < 48) return Math.round(h) + ' h ago';
    return Math.round(h / 24) + ' days ago';
  };
  const SRC_STATUS = {
    OK: 'Reading successfully', NO_CURRENT_ALERTS: 'Reading successfully (no alerts in effect)', NEEDS_API_KEY: 'Not configured (needs an API key)',
    DISABLED: 'Not enabled', BLOCKED: 'Unreachable at last attempt (last good data kept)', ERROR: 'Error at last attempt (last good data kept)', CATALOGUE_ONLY: 'Catalogue only',
  };
  const pill = (key, text) => { const L = C.LABELS[key]; return `<span class="pill ${L.cls}" title="${esc(L.tip)}">${esc(text || L.text)}</span>`; };
  const prov = o => {
    const p = [`Source: ${esc(o.source)}`];
    if (o.kind) p.push(esc(o.kind));
    if (o.dataDate) p.push('Data date: ' + fmt(o.dataDate));
    if (o.fetchedAt) p.push('Fetched: ' + fmt(o.fetchedAt));
    if (o.status) p.push('Status: ' + esc(o.status));
    return `<div class="prov">${p.join(' &middot; ')}</div>`;
  };

  async function getJSON(path) {
    try { const r = await fetch(path, { cache: 'no-store' }); if (!r.ok) return null; return await r.json(); } catch (e) { return null; }
  }

  async function loadAll() {
    const paths = {
      states: 'data/states.geojson', districts: 'data/districts.geojson', lakes: 'data/glacial_lakes.geojson', net: 'data/network/reaches.json',
      anchors: 'data/anchors.json', expo: 'data/exposure/reach_exposure.json', search: 'data/search_index.json', sources: 'data/sources.json',
      build: 'data/build_status.json', audit: 'data/audit.json', conn: 'data/connectivity_report.json', alerts: 'data/live/alerts.json',
      live: 'data/live/status.json', guide: 'data/hazard_guidance.json', coverage: 'data/coverage.json', obs: 'data/live/river_observations.json', nwdpcat: 'data/live/nwdp_catalogue.json',
    };
    await Promise.all(Object.entries(paths).map(async ([k, p]) => { D[k] = await getJSON(p); }));
    // Shape guards: a file that parses but has the wrong shape is treated as absent, never allowed to break a panel.
    ['states', 'districts', 'lakes'].forEach(k => { if (D[k] && !Array.isArray(D[k].features)) D[k] = null; });
    if (D.alerts && (typeof D.alerts !== 'object' || !Array.isArray(D.alerts.alerts))) D.alerts = D.alerts && typeof D.alerts === 'object' ? Object.assign(D.alerts, { alerts: [], malformed: true }) : null;
    if (D.obs && !Array.isArray(D.obs.observations)) D.obs = null;
    if (D.live && typeof D.live !== 'object') D.live = null;
    if (D.guide && !Array.isArray(D.guide.entries)) D.guide = null;
    G = D.net && D.net.ids ? C.makeGraph(D.net) : null;
    // A reading without finite coordinates and value cannot be plotted (NaN arrives as null): drop it instead of crashing the map.
    if (D.obs && Array.isArray(D.obs.observations)) {
      D.obs.observations = D.obs.observations.filter(o => o && Number.isFinite(o.lat) && Number.isFinite(o.lon) && Number.isFinite(o.value));
    }
    // Never show an alert as current after its own expiry time, even if the scheduled refresh is late.
    if (D.alerts && Array.isArray(D.alerts.alerts)) {
      const all = D.alerts.alerts;
      D.alerts.alerts = all.filter(a => C.alertActive(a));
      D.alerts.expired_since_refresh = all.length - D.alerts.alerts.length;
    }
  }

  // ---------------------------------------------------------------- derived data
  function sourceInfo(id) { return ((D.sources && D.sources.sources) || []).find(s => s.dataset_id === id || s.source_id === id) || null; }
  const alertList = () => (D.alerts && D.alerts.alerts) || [];
  let alertFilter = 'ALL';                       // which alert type the Signals list and the map colouring show
  const eventOf = a => (a && a.event) || 'Alert';
  const passesFilter = a => alertFilter === 'ALL' || eventOf(a) === alertFilter;
  function indexAlerts() {
    sevByDistrict = {}; alertsByDistrict = {};
    alertList().forEach(a => (a.districts || []).forEach(d => {
      (alertsByDistrict[d] = alertsByDistrict[d] || []).push(a);
      if (passesFilter(a)) sevByDistrict[d] = Math.max(sevByDistrict[d] || 0, C.severityRank(a.severity));
    }));
  }

  // ---------------------------------------------------------------- map
  function initMap() {
    map = L.map('map', { center: [23.5, 80], zoom: 5, minZoom: 4, preferCanvas: true, zoomControl: true });
    base = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 14, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    chainGroup = L.layerGroup().addTo(map);
    layers.base = base;
    map.attributionControl.addAttribution('SHAILDHARA: data from the sources listed under "Sources"');
    const key = L.control({ position: 'bottomleft' });
    key.onAdd = () => {
      const d = L.DomUtil.create('details', 'mapkey'); d.open = window.matchMedia('(min-width: 901px)').matches;
      d.innerHTML = '<summary>Map key</summary>' +
        '<div><i class="sw" style="background:#4fc3f7;border:1px solid #0a3d62;border-radius:50%"></i>Glacial lake (CWC inventory, static)</div>' +
        '<div><i class="sw" style="background:#66bb6a;border:1px solid #1b5e20;border-radius:50%"></i>Station reading (CWC/NWDP, source-reported)</div>' +
        '<div><i class="sw" style="background:#e65100;opacity:.6"></i>District with an unexpired official alert</div>' +
        '<div><i class="sw ln" style="background:#ff6f00"></i>Chain start (alert districts / lake)</div>' +
        '<div><i class="sw ln" style="background:#0b6fa4"></i>Mapped downstream reaches (computed, not a forecast)</div>' +
        '<div><i class="sw ln dash"></i>Lake to nearest river: proximity only, unverified</div>';
      L.DomEvent.disableClickPropagation(d); return d;
    };
    key.addTo(map);
  }

  function buildLayers() {
    if (D.states) {
      stateLayer = L.geoJSON(D.states, { style: { color: '#27475a', weight: 1.4, fill: false }, interactive: false }).addTo(map);
      layers.states = stateLayer;
      try { map.fitBounds(stateLayer.getBounds(), { padding: [10, 10] }); } catch (e) { /* ignore */ }
    }
    if (D.districts) {
      districtLayer = L.geoJSON(D.districts, {
        style: f => districtStyle(f.properties.did),
        onEachFeature: (f, lyr) => {
          districtById[f.properties.did] = { f, lyr };
          lyr.on('click', () => selectDistrict(f.properties.did));
          lyr.bindTooltip(esc(f.properties.name) + (f.properties.state ? ', ' + esc(f.properties.state) : ''), { sticky: true });
        },
      }).addTo(map);
      layers.districts = districtLayer;
    }
    if (D.lakes) {
      lakeLayer = L.layerGroup();
      D.lakes.features.forEach(f => {
        const p = f.properties, [lon, lat] = f.geometry.coordinates;
        lakeById[p.lid] = f;
        const m = L.circleMarker([lat, lon], { radius: 4.5, color: '#0a3d62', weight: 1, fillColor: '#4fc3f7', fillOpacity: .9 });
        m.bindTooltip(esc(p.name || ('Glacial lake ' + p.lid)));
        m.on('click', () => selectLake(p.lid));
        lakeLayer.addLayer(m);
      });
      lakeLayer.addTo(map);
      layers.lakes = lakeLayer;
    }
    if (D.obs && D.obs.observations && D.obs.observations.length) {
      obsLayer = L.layerGroup();
      D.obs.observations.forEach(o => {
        const flag = C.obsFlag(o);
        L.circleMarker([o.lat, o.lon], { radius: 5, color: flag ? '#b71c1c' : '#1b5e20', fillColor: '#66bb6a', fillOpacity: flag ? .15 : .9, weight: flag ? 2 : 1, dashArray: flag ? '3 2' : null })
          .bindTooltip(`${esc(o.station)}: ${esc(o.parameter)} ${esc(o.value)} (${esc(fmt(o.observed_at))}) - as reported by the source, not quality-checked by SHAILDHARA` + (flag ? ` <b>WARNING: ${esc(flag)}.</b>` : '')).addTo(obsLayer);
      });
      obsLayer.addTo(map);
      layers.obs = obsLayer;
    }
  }

  function districtStyle(did) {
    const s = sevByDistrict[did];
    return s ? { color: '#555', weight: .6, fillColor: SEV_COLOR[s] || '#999', fillOpacity: .5 } : { color: '#9aa7b1', weight: .4, fillColor: '#ffffff', fillOpacity: 0 };
  }
  function restyleDistricts() { if (districtLayer) districtLayer.setStyle(f => districtStyle(f.properties.did)); }

  // ---------------------------------------------------------------- status header / banner
  function renderTop() {
    const b = $('#banner');
    const missing = [];
    if (!D.states) missing.push('state outlines');
    if (!D.districts) missing.push('districts');
    if (!G) missing.push('river network');
    if (!D.states && !D.districts && !G) {
      b.hidden = false; b.className = 'banner err';
      b.innerHTML = '<strong>No data has been built yet.</strong> This site only shows real data produced by the pipeline. Run <code>python -m pipeline.build_all</code> (see README) and reload. Nothing is shown in the meantime so nothing can be mistaken for real data.';
    } else if (missing.length) {
      b.hidden = false; b.className = 'banner';
      b.innerHTML = 'Some foundation layers are not available yet: <strong>' + esc(missing.join(', ')) + '</strong>. Open the Sources tab for the exact reasons and actions.';
    } else b.hidden = true;
    const chips = [];
    const al = D.live && D.live.sources && D.live.sources.sachet;
    const ob = D.live && D.live.sources && D.live.sources.nwdp_cwc;
    if (D.live && D.live.generated_at) {
      const late = (Date.now() - Date.parse(D.live.generated_at)) / 3.6e6 > 12;
      chips.push(`<span class="chip ${late ? 'chip-warn' : 'chip-ok'}" title="Live feeds are re-read about every 3 hours when GitHub runs the scheduled job.">Live feeds refreshed ${esc(ago(D.live.generated_at))}${late ? ' (overdue)' : ''}</span>`);
    } else chips.push('<span class="chip chip-warn">Live refresh time unknown</span>');
    chips.push(D.alerts ? `<span class="chip">Official alerts in effect: <b>${alertList().length}</b></span>` : `<span class="chip chip-warn">Alerts: ${al ? esc(SRC_STATUS[al.status] || al.status) : 'not loaded'}</span>`);
    if (ob && ob.data_date) chips.push(`<span class="chip" title="Newest station reading in the data. Station readings are the latest official update, not a live stream.">Newest river reading ${esc(ago(ob.data_date))}</span>`);
    if (D.build) chips.push(`<span class="chip" title="Boundaries, river network, glacial lakes and exposure layers are static snapshots, rebuilt only when the project is rebuilt.">Map layers: static snapshot, built ${esc(fmt(D.build.stages && D.build.stages.static && D.build.stages.static.finished_at || D.build.generated_at).replace(/, \d\d:\d\d IST/, ''))}</span>`);
    $('#top-status').innerHTML = chips.join('');
    $('#legend').innerHTML = ['OFFICIAL', 'CURRENT', 'LATEST', 'COMPUTED', 'PROXIMITY', 'SCENARIO', 'NODATA'].map(k => pill(k)).join('');
  }

  // ---------------------------------------------------------------- alert helpers
  const normName = s => String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  let _distNames = null;
  function districtNameIndex() {
    if (_distNames) return _distNames;
    _distNames = {};
    ((D.districts && D.districts.features) || []).forEach(f => { const n = normName(f.properties.name); if (n.length >= 5) (_distNames[n] = _distNames[n] || []).push(f.properties); });
    return _distNames;
  }
  // Districts the alert's headline/description names but its official area list did not match. Only names that are unique in India count.
  function headlineExtraDistricts(a) {
    const text = ' ' + normName([a.headline, a.description].join(' ')) + ' ';
    const have = new Set(a.districts || []), idx = districtNameIndex(), out = [];
    for (const n in idx) if (idx[n].length === 1 && text.includes(' ' + n + ' ') && !have.has(idx[n][0].did)) out.push(idx[n][0]);
    return out;
  }
  function extraNote(a) {
    const ex = headlineExtraDistricts(a);
    if (!ex.length) return '';
    const areaText = ' ' + normName((a.areas || []).join(' ')) + ' ';
    const inArea = ex.filter(d => areaText.includes(' ' + normName(d.name) + ' '));
    const onlyHead = ex.filter(d => !inArea.includes(d));
    const list = l => l.slice(0, 12).map(d => d.name + (d.state ? ' (' + d.state + ')' : '')).join(', ') + (l.length > 12 ? ' ...' : '');
    const parts = [];
    if (inArea.length) parts.push(`the alert's area text lists ${list(inArea)}, but SHAILDHARA could not match ${inArea.length > 1 ? 'these' : 'this'} to a district outline (a spelling or naming difference)`);
    if (onlyHead.length) parts.push(`the headline also names ${list(onlyHead)}, which the alert's official area list does not include`);
    const have = (a.districts || []).map(d => (districtById[d] ? districtById[d].f.properties.name : d)).slice(0, 6).join(', ');
    return `Note: ${parts.join('; and ')}. Districts that were matched: ${have || 'none'}. SHAILDHARA does not guess, so only matched districts are used. Please read the original alert.`;
  }
  function rainEvidenceHtml(a) {
    if (!/rain/i.test((a.event || '') + ' ' + (a.headline || ''))) return '';
    const obs = (D.obs && D.obs.observations) || [];
    const geoms = (a.districts || []).map(d => districtById[d]).filter(Boolean).map(d => d.f.geometry);
    let h = `<div class="evid"><p><strong>Observed rainfall evidence</strong> ${pill('OFFICIAL')}</p>`;
    if (!geoms.length) return h + `<p class="small">${pill('NODATA')} No district could be matched to this alert, so no station can be checked.</p></div>`;
    const ev = C.rainfallEvidence(obs, geoms, 100);
    const li = o => `<li>${esc(o.station)}: ${esc(o.value)} mm, hourly reading at ${fmt(o.observed_at)} (${esc(ago(o.observed_at))}) ${C.freshness(o.observed_at, 24) === 'CURRENT' ? pill('CURRENT') : pill('LATEST', 'Older than 24 h')}${o.distKm > 0 ? ' &middot; about ' + Math.round(o.distKm) + ' km from the alerted area' : ''}${C.obsFlag(o) ? ` <span class="reason warn">${esc(C.obsFlag(o))}</span>` : ''}</li>`;
    if (ev.inside.length) h += `<p class="small">Rainfall stations inside the alerted districts (CWC/NWDP):</p><ul>${ev.inside.slice(0, 4).map(li).join('')}</ul>`;
    else if (ev.near.length) h += `<p class="small">No loaded rainfall station is inside the alerted districts. Nearest within 100 km:</p><ul>${ev.near.slice(0, 3).map(li).join('')}</ul>`;
    else h += `<p class="small">${pill('NODATA', 'No station nearby')} No loaded rainfall station is inside, or within 100 km of, the alerted districts. This does not mean no rain fell: only ${ev.rainStationsTotal} rainfall station(s) are loaded for the whole country.</p>`;
    return h + `<p class="small">These are single hourly readings from a partial set of stations and can be days old. They are not a daily rainfall total, and they neither confirm nor contradict the alert. Rainfall categories (Heavy, Very Heavy) are defined and issued by IMD.</p></div>`;
  }
  function overviewHtml() {
    const al = alertList();
    if (!D.alerts || !al.length) return '';
    const stateSet = new Set();
    al.forEach(a => (a.districts || []).forEach(d => { const x = districtById[d]; if (x && x.f.properties.state) stateSet.add(x.f.properties.state); }));
    const isRain = a => /rain/i.test((a.event || '') + ' ' + (a.headline || ''));
    const counts = {}; al.forEach(a => { counts[eventOf(a)] = (counts[eventOf(a)] || 0) + 1; });
    const fixed = ['Very Heavy Rain', 'Heavy Rain'];
    const others = Object.keys(counts).filter(k => !fixed.includes(k)).sort((x, y) => counts[y] - counts[x]);
    const btn = (key, label, n) => `<button class="sm filt${alertFilter === key ? ' on' : ''}" data-alert-filter="${esc(key)}" aria-pressed="${alertFilter === key}" ${n === 0 ? 'disabled' : ''}>${esc(label)} (${n})</button>`;
    const states = Array.from(stateSet).sort();
    return `<div class="card overview"><h3>National overview</h3>
      <div class="stats"><div class="stat"><b>${al.length}</b><span>alerts in effect</span></div><div class="stat"><b>${stateSet.size}</b><span>states / UTs matched</span></div><div class="stat"><b>${al.filter(isRain).length}</b><span>rain alerts</span></div><div class="stat"><b>${al.filter(a => C.severityRank(a.severity) >= 3).length}</b><span>Severe or higher</span></div></div>
      <div class="small">${states.length ? 'States / UTs with matched districts: ' + esc(states.join(', ')) : 'No alert area could be matched to a district yet.'}</div>
      <div class="small">Issued by: ${esc(Object.entries(al.reduce((m, a) => { const k = a.sender || 'unknown'; m[k] = (m[k] || 0) + 1; return m; }, {})).sort((x, y) => y[1] - x[1]).map(e => e[0] + ' (' + e[1] + ')').join(', '))}</div>
      <div class="small" style="margin-top:6px">Show only:</div>
      <div class="row" id="alert-filters">${btn('ALL', 'All', al.length)}${fixed.map(k => btn(k, k, counts[k] || 0)).join('')}${others.map(k => btn(k, k, counts[k])).join('')}</div>
      <div class="small">${pill('OFFICIAL')} The map colours districts by the alerts being shown. These are counts of alerts, not storms: the same weather can appear in alerts from several agencies. States come from the districts each alert was matched to, so areas that could not be matched are missed. Severity is as written by the issuing agency.</div></div>`;
  }
  function setAlertFilter(k) {
    alertFilter = k; indexAlerts(); restyleDistricts();
    const left = $('#left'), top = left ? left.scrollTop : 0;
    renderSignals(); if (left) left.scrollTop = top;
  }

  // ---------------------------------------------------------------- signals tab
  function alertCard(a, i) {
    const sev = C.severityRank(a.severity);
    const entry = G && D.anchors ? C.entryReachesForDistricts(G, D.anchors, a.districts, 40) : [];
    const can = entry.length > 0;
    const why = !a.districts.length ? 'The place names in this alert could not be matched to a district, so no river path can be drawn. Open it to see the alert as issued and what this kind of event can lead to.'
      : !G ? 'The river network is not loaded, so no river path can be drawn. Open it to see the alert as issued.' : (!can ? 'No mapped river reaches were found inside the alerted districts. Open it to see the alert as issued and what this kind of event can lead to.' : '');
    const status = C.alertActive(a) ? 'CURRENT' : 'STALE';
    return `<div class="card" data-alert="${i}">
      <h3>${esc(a.event || a.headline || 'Alert')}</h3>
      <div>${pill('OFFICIAL')}${status === 'CURRENT' || !a.expires ? pill('CURRENT') : ''}<span class="pill sev sev-${sev}">${esc(a.severity || 'severity not given')}</span></div>
      <div class="meta">${esc(a.sender || 'sender not given')} &middot; issued ${fmt(a.sent)}${a.expires ? ' &middot; expires ' + fmt(a.expires) : ''}</div>
      <div class="meta">${esc((a.areas || []).slice(0, 3).join('; '))}${(a.areas || []).length > 3 ? ' ...' : ''}</div>
      ${C.nonEnglish(a.description || a.headline) ? '<div class="meta">Text is shown exactly as issued, in the agency\'s language (not translated).</div>' : ''}
      <div class="meta">Districts matched: ${a.districts.length}${a.districts_stated ? ' of ' + a.districts_stated + ' stated' : ''} (${esc(MATCH_LABEL[a.match_method] || a.match_method)})</div>
      <button class="chain-btn${can ? '' : ' secondary'}" data-chain-alert="${i}">${can ? 'SHOW ME THE CHAIN' : 'OPEN ALERT DETAILS'}</button>
      ${why ? `<div class="reason">${esc(why)}</div>` : ''}
      ${extraNote(a) ? `<div class="reason warn">${esc(extraNote(a))}</div>` : ''}
      ${prov({ source: 'NDMA SACHET (' + (a.sender || 'issuing agency') + ')', kind: 'WARNING (official, as issued)', dataDate: a.sent, fetchedAt: D.alerts.generated_at, status: 'CURRENT' })}
    </div>`;
  }

  function sourceStatusCards() {
    const src = (D.live && D.live.sources) || {};
    const keys = Object.keys(src);
    if (!keys.length) return `<div class="card">${pill('NODATA', 'Status not loaded')} <div class="small">The live-source status file could not be read, so refresh times cannot be shown.</div></div>`;
    return keys.map(k => {
      const s = src[k];
      const ok = s.status === 'OK';
      const ev = ok ? pill('OFFICIAL') + pill(C.freshness(s.data_date, 48) === 'CURRENT' ? 'CURRENT' : 'LATEST') : pill('NODATA');
      return `<div class="card"><h3>${esc(s.name || k)}</h3>${ev}<span class="small"> ${esc(s.status)}</span>
        <div class="meta">${esc(s.detail || '')}</div>
        ${(s.blockers || []).slice(0, 3).map(b => `<div class="reason warn">Blocker: ${esc(b)}</div>`).join('')}
        ${prov({ source: s.name || k, kind: s.kind, dataDate: s.data_date, fetchedAt: s.fetched_at })}</div>`;
    }).join('');
  }

  function renderSignals() {
    const el = $('#tab-signals');
    const al = alertList();
    let h = '<h2>Current official alerts</h2>' + overviewHtml();
    const ss = D.live && D.live.sources && D.live.sources.sachet;
    if (D.alerts && ss && (ss.status === 'BLOCKED' || ss.status === 'ERROR')) {
      h += `<div class="card">${pill('LATEST')}<div class="small"><b>The official alert feed could not be read in the latest refresh.</b> Alerts shown are from the last successful read (${esc(fmt(D.alerts.generated_at))}) and may have been updated or cancelled since. Check sachet.ndma.gov.in for the live position.</div></div>`;
    }
    if (!D.alerts) h += `<div class="card">${pill('NODATA', 'Alerts not loaded')}<div class="small">The alert file could not be read. Check sachet.ndma.gov.in for current official alerts.</div></div>`;
    else if (!al.length) h += `<div class="card">${pill('CURRENT', 'No alerts in effect')}<div class="small">No unexpired alerts in the feeds read at ${fmt(D.alerts.generated_at)}${D.alerts.expired_since_refresh ? ' (' + D.alerts.expired_since_refresh + ' alert(s) in that file have since expired and are hidden)' : ''}. This usually means a quiet period; if a feed was unreachable, the source status below says so.</div></div>`;
    else {
      if (D.alerts.expired_since_refresh) h += `<div class="small">${D.alerts.expired_since_refresh} alert(s) from the ${fmt(D.alerts.generated_at)} refresh have expired and are hidden.</div>`;
      if (alertFilter !== 'ALL') h += `<div class="small">Showing only <b>${esc(alertFilter)}</b> alerts. Choose <b>All</b> above to see every alert.</div>`;
      const order = al.map((a, i) => i).filter(i => passesFilter(al[i])).sort((x, y) => C.severityRank(al[y].severity) - C.severityRank(al[x].severity));
      h += order.slice(0, 80).map(i => alertCard(al[i], i)).join('');
      if (order.length > 80) h += `<div class="small">Showing the 80 highest-severity alerts of ${order.length}.</div>`;
    }
    h += '<h2>Live source status</h2>' + sourceStatusCards();
    h += '<h2>Mountain inventory: glacial lakes</h2>';
    if (!D.lakes) h += `<div class="card">${pill('NODATA', 'Layer not loaded')}<div class="small">The glacial-lake layer could not be read.</div></div>`;
    else {
      const states = Array.from(new Set(D.lakes.features.map(f => f.properties.state).filter(Boolean))).sort();
      h += `<div class="small">${pill('LATEST', 'Latest official update')} Static CWC inventory (data date ${esc((sourceInfo('glacial_lakes_cwc') || {}).data_date || 'unknown')}), not a live feed.</div>
        <div class="row"><select id="lake-state" aria-label="State"><option value="">All states</option>${states.map(s => `<option>${esc(s)}</option>`).join('')}</select></div>
        <div id="lake-list"></div>`;
    }
    el.innerHTML = h;
    renderLakeList();
    const sel = $('#lake-state'); if (sel) sel.onchange = renderLakeList;
  }

  function renderLakeList() {
    const box = $('#lake-list'); if (!box || !D.lakes) return;
    const st = ($('#lake-state') || {}).value || '';
    let fs = D.lakes.features.filter(f => !st || f.properties.state === st);
    fs = fs.sort((a, b) => (b.properties.area_ha_calc || 0) - (a.properties.area_ha_calc || 0));
    box.innerHTML = fs.slice(0, 40).map(f => {
      const p = f.properties;
      return `<div class="card" data-lake="${esc(p.lid)}"><h3>${esc(p.name || ('Glacial lake ' + p.lid))}</h3>
        <div class="meta">${esc(p.state || 'state unknown')} &middot; area ${p.area_ha_calc != null ? (+p.area_ha_calc).toFixed(1) + ' ha (calculated from outline)' : 'unknown'}</div>
        <div class="meta">Nearest mapped river: ${p.snap_km != null ? esc(p.snap_km) + ' km' : 'none within 10 km'} (${C.lakeLink(p.quality, p.snap_km).trace ? 'proximity only, drainage unverified' : 'too far or absent: no connection shown'})</div></div>`;
    }).join('') + (fs.length > 40 ? `<div class="small">Showing the 40 largest of ${fs.length}. Use Search for others.</div>` : '');
  }

  // ---------------------------------------------------------------- layers tab
  function renderLayersTab() {
    const rows = [
      ['base', 'OpenStreetMap basemap', true, !!base], ['states', 'State/UT outlines', true, !!stateLayer], ['districts', 'District outlines + alert shading', true, !!districtLayer],
      ['lakes', 'Glacial lakes (static inventory)', true, !!lakeLayer], ['obs', 'River/rain observation stations (NWDP/CWC)', true, !!obsLayer],
    ];
    let h = '<h2>Map layers</h2>';
    rows.forEach(([k, label, on, avail]) => {
      h += `<label class="layer-row"><input type="checkbox" data-layer="${k}" ${on && avail ? 'checked' : ''} ${avail ? '' : 'disabled'}> ${esc(label)} ${avail ? '' : pill('NODATA', 'Not loaded')}</label>`;
    });
    h += `<h2>Alert shading key</h2><div class="small">District fill follows the CAP severity written by the issuing agency: ` +
      [4, 3, 2, 1].map(s => `<span class="pill" style="background:${SEV_COLOR[s]}">${['', 'Minor', 'Moderate', 'Severe', 'Extreme'][s]}</span>`).join('') + '</div>';
    $('#tab-layers').innerHTML = h;
    $$('#tab-layers input[data-layer]').forEach(cb => cb.onchange = () => {
      const lyr = layers[cb.dataset.layer]; if (!lyr) return;
      cb.checked ? lyr.addTo(map) : map.removeLayer(lyr);
    });
  }

  // ---------------------------------------------------------------- search tab
  function runSearch(q) {
    const out = $('#results'); q = q.trim().toLowerCase();
    if (q.length < 2) { out.innerHTML = '<div class="small">Type at least 2 letters.</div>'; return; }
    const hits = [];
    (D.search || []).forEach(s => { if ((s.n || '').toLowerCase().includes(q) || (s.x || '').toLowerCase().includes(q)) hits.push({ t: s.t, id: s.id, n: s.n, x: s.x, lat: s.lat, lon: s.lon }); });
    alertList().forEach((a, i) => { if (((a.event || '') + ' ' + (a.areas || []).join(' ')).toLowerCase().includes(q)) hits.push({ t: 'alert', id: i, n: a.event || 'Alert', x: (a.areas || [])[0] }); });
    out.innerHTML = hits.slice(0, 60).map(h => `<div class="card" data-hit="${esc(h.t)}|${esc(h.id)}"><h3>${esc(h.n)}</h3><div class="meta">${esc(h.t)}${h.x ? ' &middot; ' + esc(h.x) : ''}</div></div>`).join('') || '<div class="small">Nothing found.</div>';
  }

  // ---------------------------------------------------------------- sources tab
  const CATLABEL = { AVAILABLE_VERIFIED: ['Available and verified', 'ev-current'], AVAILABLE_INCOMPLETE: ['Available but incomplete', 'ev-latest'],
    STATIC_OR_PERIODIC: ['Static or periodic', 'ev-official'], BLOCKED: ['Blocked or unavailable', 'ev-nodata'], PLANNED: ['Planned, not implemented', 'ev-scenario'] };
  function renderCoverageTab() {
    const el = $('#tab-coverage'), c = D.coverage;
    if (!c || !c.summary) { el.innerHTML = '<h2>Coverage audit</h2><div class="card"><span class="pill ev-nodata">Not available</span><div class="small">The coverage audit file could not be read.</div></div>'; return; }
    const s = c.summary;
    let h = '<h2>How to read the evidence</h2>' + [
      ['OFFICIAL', 'Current observations and alerts', 'Official alerts (NDMA SACHET) and CWC/NWDP station readings, shown as issued with their own dates. Station readings are the latest official update, not a live stream, and cover only the stations whose datasets are machine-readable.'],
      ['LATEST', 'Historical inventories', 'Glacial lakes, river lines, state and district boundaries. These are dated snapshots from official portals. A lake being mapped does not mean it is dangerous or has changed.'],
      ['COMPUTED', 'Computed spatial relationships', 'River paths, districts along them and nearby assets are calculated from map geometry. Lake-to-river links are nearest-river matches only and are not confirmed drainage. Not a forecast, not an official route.'],
      ['SCENARIO', 'Scenarios and settings', 'The downstream display distance and the suggested checks are user-facing aids, not predictions.'],
    ].map(r => '<div class="card">' + pill(r[0], r[1]) + '<div class="small">' + esc(r[2]) + '</div></div>').join('') +
    '<h2>Coverage audit</h2><div class="small">Counted from the data this site serves (built ' + esc(fmt(c.generated_at)) + '). ' + esc(c.honesty) + '</div>';
    h += '<div class="stats">' + [[s.states_represented + '/28', 'states'], [s.uts_represented + '/8', 'union territories'], [s.districts, 'districts'], [s.glacial_lakes, 'glacial lakes'],
      [s.river_reaches_total, 'river reaches in graph'], [s.alerts_unexpired, 'unexpired alerts'], [s.stations, 'stations read'], [s.exposure_layers_populated, 'exposure layers']]
      .map(x => '<div class="stat"><b>' + esc(x[0]) + '</b><span>' + esc(x[1]) + '</span></div>').join('') + '</div>';
    if (s.missing && s.missing.length) h += '<div class="small warn">Not represented: ' + esc(s.missing.join(', ')) + '</div>';
    Object.keys(CATLABEL).forEach(k => {
      const rows = c.layers.filter(l => l.category === k); if (!rows.length) return;
      h += '<h3 class="cat"><span class="pill ' + CATLABEL[k][1] + '">' + CATLABEL[k][0] + '</span></h3>';
      rows.forEach(l => { h += '<div class="card"><b>' + esc(l.label) + '</b><div class="meta">' + esc(l.update) + ' &middot; records: ' + esc(l.records == null ? 'n/a' : l.records) +
        ' &middot; data date: ' + esc(l.data_date ? fmt(l.data_date) : 'n/a') + '</div><div class="small">' + esc(l.note) + '</div>' +
        (l.source_url ? '<div class="small"><a href="' + esc(l.source_url) + '" target="_blank" rel="noopener">Source</a></div>' : '') + '</div>'; });
    });
    h += '<h3>By region</h3><div class="tablewrap"><table><tr><th>Region</th><th>Dist.</th><th>Lakes</th><th>Reaches</th><th>Stations</th><th>Alerts</th></tr>' +
      c.regions.map(r => '<tr><td>' + esc(r.name) + '<div class="small">' + esc(r.kind) + '</div></td><td>' + r.districts + '</td><td>' + r.glacial_lakes + '</td><td>' + r.river_reaches +
        '</td><td>' + (r.stations == null ? 'n/a' : r.stations) + '</td><td>' + r.active_alerts + '</td></tr>').join('') + '</table></div>' +
      '<div class="small">A zero means none in the data served, not that none exist. Station and lake coverage is uneven by design of the sources.</div>';
    el.innerHTML = h;
  }

  function freshnessTable() {
    const rows = [];
    const live = (D.live && D.live.sources) || {};
    Object.keys(live).forEach(k => {
      const v = live[k], ok = v.status === 'OK' || v.status === 'NO_CURRENT_ALERTS';
      rows.push([v.name || k, ok ? 'Live feed' : 'Live feed (not reading)', v.data_date ? fmt(v.data_date) + (ago(v.data_date) ? ' (' + ago(v.data_date) + ')' : '') : 'none', v.fetched_at ? fmt(v.fetched_at) + ' (' + ago(v.fetched_at) + ')' : 'never', SRC_STATUS[v.status] || v.status || 'unknown']);
    });
    ((D.sources && D.sources.sources) || []).filter(x => !live[x.source_id] && x.source_id !== 'sachet' && x.source_id !== 'nwdp_cwc').forEach(x => {
      rows.push([x.title || x.source_id, /unavailable/.test(x.dataset_id || '') ? 'Not loaded' : 'Static snapshot', x.data_date || 'unknown', x.retrieved_at ? fmt(x.retrieved_at) : 'n/a', x.status || 'n/a']);
    });
    if (!rows.length) return '';
    return '<h2>Data freshness</h2><div class="small">Only rows marked <b>Live feed</b> are refreshed on a schedule. Everything else is a snapshot from the date shown.</div><div class="tablewrap"><table><tr><th>Dataset</th><th>Type</th><th>Data date</th><th>Last checked</th><th>Status</th></tr>' +
      rows.map(r => '<tr>' + r.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>').join('') + '</table></div>';
  }

  function renderSourcesTab() {
    let h = freshnessTable() + '<details class="provdetails"><summary>Technical details, blockers and limitations</summary>';
    h += '<h2>Build status and blockers</h2>';
    if (!D.build) h += `<div class="card">${pill('NODATA', 'Not available')}<div class="small">The build report could not be read.</div></div>`;
    else {
      h += `<div class="small">Built ${fmt(D.build.generated_at)}</div>`;
      const bl = D.build.blockers || [];
      h += bl.length ? '<ul>' + bl.map(b => `<li class="small warn">${esc(b)}</li>`).join('') + '</ul>' : '<div class="small">No blockers recorded.</div>';
      if ((D.build.actions || []).length) h += '<h2>Actions needed from the project owner</h2><ul>' + D.build.actions.map(a => `<li class="small">${esc(a)}</li>`).join('') + '</ul>';
    }
    if (D.conn && D.conn.stats) {
      const s = D.conn.stats;
      h += `<h2>River connectivity</h2><div class="small">Reaches exported: ${esc(s.exported_reaches)}; lake links: ${esc(JSON.stringify(s.lake_links || {}))}; path ends: ${esc(JSON.stringify(s.path_end_reasons || {}))}. Downstream links come from ${esc((D.net && D.net.source_label) || 'the river network dataset named in the source list')}. Lake-to-river links are nearest-reach (proximity) matches only.</div>`;
    }
    if (D.audit && D.audit.layers) {
      h += '<h2>Data audit</h2><table><tr><th>Layer</th><th>Features</th><th>Invalid</th><th>Dup IDs</th></tr>' +
        D.audit.layers.map(a => `<tr><td>${esc(a.layer)}</td><td>${esc(a.features)}</td><td>${esc(a.invalid_geometries)}</td><td>${esc(a.duplicate_id_rows)}</td></tr>`).join('') + '</table>';
    }
    h += '</details><h2>Datasets and feeds</h2>';
    const rows = (D.sources && D.sources.sources) || [];
    if (!rows.length) h += `<div class="card">${pill('NODATA', 'Not available')}<div class="small">No source records could be read.</div></div>`;
    rows.forEach(s => {
      h += `<div class="card"><h3>${esc(s.title || s.source_id)}</h3>
        <div>${s.authority === 'indian_government' ? pill('OFFICIAL', 'Indian government source') : '<span class="pill ev-nodata">International/open gap-filler</span>'}
        <span class="pill ev-latest">${esc(s.kind || 'STATIC')}</span></div>
        <div class="meta">${esc(s.organisation || '')}</div>
        <div class="meta">Data date/version: ${esc(s.data_date || 'unknown')} &middot; retrieved ${fmt(s.retrieved_at)} &middot; ${esc(s.status || '')}</div>
        <div class="meta">URL status: ${esc(s.url_status || 'n/a')}</div>
        <div class="meta">Coverage: ${esc(s.coverage || 'n/a')}</div>
        <div class="meta">Licence/access: ${esc(s.licence || 'n/a')}</div>
        <div class="meta warn">Limitations: ${esc(s.limitations || s.note || 'n/a')}</div>
        <div class="prov">${safeUrl(s.url) ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">source URL</a> &middot; ` : ''}${s.sha256 ? 'SHA-256 ' + esc(String(s.sha256).slice(0, 16)) + '...' : 'no checksum (live feed or not downloaded)'}</div></div>`;
    });
    $('#tab-sources').innerHTML = h;
  }

  // ---------------------------------------------------------------- selection
  function selectDistrict(did) {
    const d = districtById[did]; if (!d) return;
    current = { type: 'district', did, name: d.f.properties.name, state: d.f.properties.state };
    openChain(current);
  }
  function selectLake(lid) {
    const f = lakeById[lid]; if (!f) return;
    current = { type: 'lake', lid, props: f.properties, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] };
    openChain(current);
  }
  function selectAlert(i) {
    const a = alertList()[i]; if (!a) return;
    current = { type: 'alert', alert: a, i };
    openChain(current);
  }

  // ---------------------------------------------------------------- the chain
  function startsFor(sig) {
    if (!G) return { starts: [], note: 'River network is not available.' };
    if (sig.type === 'lake') {
      const r = sig.props.reach, ll = C.lakeLink(sig.props.quality, sig.props.snap_km);
      if (r == null) return { starts: [], note: 'No mapped river reach lies within 10 km of this lake in the network data, so no river connection is shown.' };
      if (!ll.trace) return { starts: [], note: 'The nearest mapped river reach is ' + sig.props.snap_km + ' km from this lake. That is too far to treat as a connection, so no downstream path is drawn. Nothing in the data shows where this lake drains.' };
      return { starts: [r] };
    }
    const dids = sig.type === 'alert' ? sig.alert.districts : [sig.did];
    if (sig.type === 'alert' && !dids.length) {
      const named = (sig.alert.areas || []).filter((x, i, arr) => arr.indexOf(x) === i).slice(0, 3).join('; ');
      return { starts: [], note: 'The area named in this alert' + (named ? ' (' + named + ')' : '') + ' is a stretch of coast, a sub-district area, an abbreviation or a state-level phrase, not a district that matches SHAILDHARA\'s district boundaries. SHAILDHARA does not guess, so no river path is drawn. The alert, its official wording and the official guidance for this kind of event are shown in steps 1 and 2.' };
    }
    const starts = D.anchors ? C.entryReachesForDistricts(G, D.anchors, dids, 40) : [];
    return starts.length ? { starts } : { starts: [], note: 'No mapped river reaches lie inside these districts in the network data.' };
  }

  function closeChain() { anim.token++; $('#chainpanel').hidden = true; chainGroup.clearLayers(); current = null; $$('.card.sel').forEach(c => c.classList.remove('sel')); }

  function openChain(sig) {
    anim.token++;
    const panel = $('#chainpanel');
    panel.hidden = false;
    map.invalidateSize();
    if (!window.matchMedia('(min-width: 901px)').matches) {
      const y = panel.getBoundingClientRect().top + window.scrollY - $('#mapwrap').offsetHeight - 6;
      window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
    }
    const title = sig.type === 'alert' ? (sig.alert.event || 'Official alert') : sig.type === 'lake' ? (sig.props.name || 'Glacial lake ' + sig.lid) : sig.name + (sig.state ? ', ' + sig.state : '');
    const kind = sig.type === 'alert' ? 'Current official alert' : sig.type === 'lake' ? 'Glacial lake (static inventory)' : 'District';
    panel.innerHTML = `<button class="sm close" id="chain-close" aria-label="Close">Close</button>
      <h2 style="margin-top:0">${esc(title)}</h2><div class="small">${esc(kind)}</div>
      <div class="row"><label class="small" for="horizon">Scenario horizon (how far downstream to show):</label>
      <select id="horizon"><option value="25">25 km</option><option value="50">50 km</option><option value="100">100 km</option><option value="250">250 km</option><option value="1000000">Whole traced path</option></select>
      <button class="sm" id="replay">Replay</button></div>
      <div class="small">${pill('SCENARIO', 'Scenario horizon')} A display setting, not a forecast of how far anything will travel.</div>
      <div id="chain-body"></div>`;
    $('#horizon').value = String(horizonKm >= 1000000 ? 1000000 : horizonKm);
    $('#chain-close').onclick = closeChain;
    $('#horizon').onchange = e => { horizonKm = +e.target.value; renderChain(); };
    $('#replay').onclick = () => renderChain();
    renderChain();
  }

  function guideSource(g) {
    const u = safeUrl(g.source.url);
    return '<p class="small">' + esc(g.source.institution) + ': ' + (u ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(g.source.title) + '</a>' : esc(g.source.title)) + '</p>';
  }
  function guideList(g, key, lead) {
    const items = (g[key] || []);
    if (!items.length) return '';
    return '<p>' + pill('OFFICIAL', 'Official guidance') + ' ' + esc(lead) + '</p><ul>' + items.map(i => '<li>' + esc(i) + '</li>').join('') + '</ul>';
  }

  function methodNote() {
    const o = D.conn && D.conn.stats && D.conn.stats.osm_network;
    const forks = o && o.forks_resolved_by_rule != null ? Number(o.forks_resolved_by_rule).toLocaleString('en-IN') : null;
    return 'Downstream direction comes from how volunteers drew each waterway in OpenStreetMap, not from a hydrological model or an official river map.'
      + (forks ? ' Where a river forks (' + forks + ' forks in the network), a fixed rule picks one branch.' : '')
      + ' Gaps and mis-drawn waterways can break or redirect a path.';
  }

  function stepHtml(n, title, body, on) { return `<div class="step ${on ? 'on' : ''}" data-step="${n}"><h3><span class="n">${n}</span>${esc(title)}</h3>${body}</div>`; }

  function renderChain() {
    const sig = current; if (!sig) return;
    const token = ++anim.token;
    chainGroup.clearLayers();
    const { starts, note } = startsFor(sig);
    const maxKm = horizonKm >= 1000000 ? null : horizonKm;
    const traced = starts.length ? C.traceMany(G, starts, maxKm) : null;
    const sum = traced ? C.summarise(G, traced) : null;
    const expo = traced ? C.sumExposure(D.expo, traced.idx) : [];
    const steps = [];

    // 1. what is changing
    let s1 = '';
    if (sig.type === 'alert') {
      const a = sig.alert;
      s1 = `<p><strong>${esc(a.event || 'Alert')}</strong>${a.headline ? ': ' + esc(a.headline) : ''}</p>
        <p>${pill('OFFICIAL')}${pill('CURRENT')}</p>
        <p class="small">Issued by ${esc(a.sender || 'unknown sender')} at ${fmt(a.sent)}${a.expires ? ', expires ' + fmt(a.expires) : ''}. Severity ${esc(a.severity || 'not given')}, urgency ${esc(a.urgency || 'not given')}, certainty ${esc(a.certainty || 'not given')} (as written by the agency).</p>
        ${prov({ source: 'NDMA SACHET / ' + (a.sender || 'issuing agency'), kind: 'WARNING (as issued)', dataDate: a.sent, fetchedAt: D.alerts && D.alerts.generated_at, status: 'CURRENT' })}
        ${(a.areas || []).length ? `<p class="small">Area named in the alert: ${esc((a.areas || []).filter((x, i, arr) => arr.indexOf(x) === i).slice(0, 6).join('; '))}${a.districts.length ? '' : ' (not matched to a district)'}</p>` : ''}
        ${extraNote(a) ? `<p class="reason warn">${esc(extraNote(a))}</p>` : ''}${rainEvidenceHtml(a)}`;
    } else if (sig.type === 'lake') {
      const p = sig.props, si = sourceInfo('glacial_lakes_cwc');
      s1 = `<p><strong>${esc(p.name || 'Glacial lake ' + p.lid)}</strong>${p.state ? ', ' + esc(p.state) : ''}. Outline area ${p.area_ha_calc != null ? (+p.area_ha_calc).toFixed(1) + ' ha (calculated)' : 'unknown'}.</p>
        <p>${pill('LATEST', 'Historical inventory')}${pill('NODATA', 'Lake change: not published as data')}</p>
        <p class="small">This is a mapped location from the CWC glacial-lake inventory, not a current observation. CWC reports lake change in monthly PDF bulletins that cannot be read automatically, so SHAILDHARA shows no current change for this lake.</p>
        ${prov({ source: 'CWC glacial lakes via NWDP', kind: 'STATIC inventory', dataDate: si && si.data_date, fetchedAt: si && si.retrieved_at, status: 'LATEST OFFICIAL' })}`;
    } else {
      const al = alertsByDistrict[sig.did] || [];
      s1 = al.length ? `<p>${al.length} current official alert(s) cover this district:</p><ul>${al.slice(0, 5).map(a => `<li>${esc(a.event || 'Alert')} (${esc(a.severity || '?')}, ${esc(a.sender || 'agency')})</li>`).join('')}</ul><p>${pill('OFFICIAL')}${pill('CURRENT')}</p>`
        : `<p>${pill('NODATA', 'No alert in effect')} None of the official alerts loaded at the last refresh covers this district.</p><p class="small">The chain below shows which rivers cross the district. It is a map exercise, not a response to an event.</p>`;
    }
    steps.push(stepHtml(1, 'What is changing', s1, true));

    // 2. what this kind of event can lead to: official guidance first, then the agency's own wording for this alert
    let s2;
    const gd = sig.type === 'alert' ? C.guidanceFor(D.guide && D.guide.entries, 'alert', sig.alert.event || sig.alert.headline)
      : sig.type === 'lake' ? C.guidanceFor(D.guide && D.guide.entries, 'lake', '') : null;
    if (sig.type === 'alert') {
      const a = sig.alert;
      s2 = (gd ? guideList(gd, 'leads_to', 'What ' + gd.label.toLowerCase() + ' can lead to, as described by the agency that issues these warnings:') + guideSource(gd)
              + '<p class="small">This is general official guidance for this type of event. It is not a forecast for this location.</p>'
              : '<p class="small">' + esc((a.event || 'This alert') + ' does not name a hazard that SHAILDHARA holds official guidance for yet. The issuing agency\'s own text is shown as issued below, and the original alert has the full detail.') + '</p>')
        + `<p>${pill('OFFICIAL', 'This alert, as issued')}</p>
        <p class="small">${esc(a.description || a.headline || 'The issuing agency gave no description text for this alert.')}</p>${a.instruction ? `<p class="small">Instruction as issued: ${esc(a.instruction)}</p>` : ''}
        ${safeUrl(a.cap_url) ? `<p class="small"><a href="${esc(safeUrl(a.cap_url))}" target="_blank" rel="noopener">Original alert (CAP)</a></p>` : ''}`;
    } else if (sig.type === 'lake') {
      s2 = gd ? guideList(gd, 'leads_to', 'What glacial lakes are officially associated with:') + guideSource(gd) + (gd.note ? '<p class="small">' + esc(gd.note) + '</p>' : '')
              : `<p>${pill('NODATA', 'Not available')} No official guidance entry is loaded for glacial lakes.</p>`;
    } else s2 = `<p>${pill('NODATA', 'No event selected')} No alert covers this district right now. Choose an alert or a glacial lake to see what that kind of event can lead to.</p>`;
    steps.push(stepHtml(2, 'What it could lead to', s2));

    // 3. water connection
    let s3;
    if (!traced) s3 = `<p>${pill('NODATA', 'No connection shown')} ${esc(note || 'No connection could be computed from the available data.')}</p>`;
    else if (sig.type === 'lake') {
      const p = sig.props, ll = C.lakeLink(p.quality, p.snap_km);
      s3 = `<p>${pill('PROXIMITY')} ${esc(ll.text.charAt(0).toUpperCase() + ll.text.slice(1))} (${esc(p.snap_km)} km to the nearest reach).</p>
        <p class="small"><strong>Drainage connection unverified.</strong> The river was chosen only because it is the closest one in the map data. The lake inventory does not say where the lake drains, and distance alone does not prove it. The dashed line on the map marks this gap. Rivers on the path: ${sum.rivers.length ? esc(sum.rivers.slice(0, 4).join(', ')) : 'unnamed in the dataset'}.</p>`;
    } else {
      s3 = `<p>${pill('COMPUTED')} ${starts.length} mapped river reach(es) lie inside the district${sig.type === 'alert' ? 's covered by the alert' : ''} (chosen as the ${esc((D.net && D.net.rank_label) || 'largest by modelled discharge')}). This is geographic overlap with the district, not a link to the cause of the alert.</p>
        <p class="small">Rivers: ${sum.rivers.length ? esc(sum.rivers.slice(0, 6).join(', ')) : 'unnamed in the dataset'}. Reaches come from ${esc((D.net && D.net.method === 'osm_waterways') ? 'OpenStreetMap waterways' : 'HydroRIVERS')}; ${esc((D.net && D.net.caveat) || 'small streams are missing at this resolution')}.</p>`;
    }
    steps.push(stepHtml(3, 'Water / river / lake connection', s3));

    // 4. downstream pathway
    let s4;
    if (!traced) s4 = `<p>${pill('NODATA', 'No path drawn')} See the note in step 3.</p>`;
    else {
      const reasons = traced.reasons;
      const endText = reasons.terminal ? ((D.net && D.net.terminal_text) || 'reaches the end of the network (sea or inland sink)') : '';
      const leave = reasons.leaves_selection ? 'leaves the part of the dataset that touches India (continues outside it; transboundary rivers are not followed)' : '';
      const hz = reasons.horizon ? `stopped at the ${maxKm} km display horizon` : '';
      s4 = `<p>${pill('COMPUTED')}${maxKm ? pill('SCENARIO', 'Scenario horizon ' + maxKm + ' km') : ''}</p>
        <p>${Math.round(traced.totalKm).toLocaleString()} km of river reaches shown, ${sum.districts.length} district(s), ${new Set(sum.states).size} state(s). Path ${[endText, leave, hz].filter(Boolean).join('; ') || 'ends for an unrecorded reason'}.</p>
        <p class="small">Districts downstream (order of first contact, by reach midpoint, approximate): ${esc(sum.districts.slice(0, 14).map(d => d.n + (d.s ? ' (' + d.s + ')' : '')).join(' > ')) || 'none mapped'}${sum.districts.length > 14 ? ' ...' : ''}</p>
        <p class="small">This is where the mapped water network connects to, not where an event will go. ${esc(methodNote())}</p>`;
      const obs = (D.obs && D.obs.observations) || [];
      if (obs.length) {
        const near = C.stationsNearPath(G, traced.idx, obs, 5).slice(0, 8);
        s4 += near.length ? `<p class="small">${pill('OFFICIAL')} Official observations within 5 km of the path:</p><ul>${near.map(o => `<li>${esc(o.station)}: ${esc(o.parameter)} ${esc(o.value)} at ${fmt(o.observed_at)} ${C.freshness(o.observed_at, 24) === 'CURRENT' ? pill('CURRENT') : pill('LATEST', 'Older than 24 h')}</li>`).join('')}</ul>`
          : '<p class="small">No loaded official station lies within 5 km of this path.</p>';
      } else s4 += `<p class="small">${pill('NODATA', 'No station readings')} No NWDP/CWC station readings are loaded right now (details under Data &amp; Sources).</p>`;
    }
    steps.push(stepHtml(4, 'Downstream pathway', s4));

    // 5. exposure
    let s5;
    if (!traced) s5 = `<p>${pill('NODATA', 'Not computed')} Without a path there is nothing to measure along.</p>`;
    else {
      const rows = expo.map(e => `<tr><td>${esc(e.label || e.key)}</td><td>${e.total == null ? pill('NODATA') : esc(Math.round(e.total).toLocaleString()) + ' ' + esc(e.unit || '')}</td><td>${e.covered}/${e.of}</td></tr>`).join('');
      const un = ((D.expo && D.expo.unavailable) || []).map(u => `<li class="small">${esc(u.key)}: ${esc(u.reason)}</li>`).join('');
      s5 = `<p>${pill('COMPUTED')} Assets lying within ${D.expo ? D.expo.corridor_m / 1000 : 2} km of the traced river reaches (spatial association only).</p>
        ${rows ? `<table><tr><th>Layer</th><th>Total</th><th>Reaches covered</th></tr>${rows}</table>` : `<p>${pill('NODATA')} No exposure layer has been built yet.</p>`}
        ${un ? `<p class="small">Not available:</p><ul>${un}</ul>` : ''}
        <p class="small">Where a layer has no coverage it is marked Not available, never counted as zero. Population is modelled, OpenStreetMap is crowd-sourced, and land cover depends on the tiles supplied. ${expo.length ? '' : ''}</p>
        ${expo.map(e => `<div class="prov">${esc(e.label)}: ${esc(e.dataset || '')}. ${esc(e.caveats || '')}</div>`).join('')}`;
    }
    steps.push(stepHtml(5, 'Current exposure along the path', s5));

    // 6. consequences: what the official guidance lists; no loss or population figures
    const cons = gd && gd.consequences && gd.consequences.length
      ? guideList(gd, 'consequences', 'Consequences listed by the agency for this type of event:') + guideSource(gd) : '';
    steps.push(stepHtml(6, 'Potential consequences',
      (cons || `<p>${pill('NODATA', 'Not listed')} No official consequence list is loaded for this type of event.</p>`) +
      `<p class="small">Figures for economic loss or people affected are left out until a validated official source exists for them. ${traced ? 'The exposure above shows what is located near the river; ' : ''}What is actually affected depends on the real event and is for the responsible authorities to judge.</p>`));

    // 7. investigate
    const rv = traced && sum.rivers.length ? sum.rivers.slice(0, 3).join(', ') : 'the rivers shown on the map';
    const ds = traced ? sum.districts.slice(0, 5).map(d => d.n).join(', ') : '';
    const checks = [];
    if (sig.type === 'alert') checks.push('Read the full alert text from the issuing agency (link in step 2).');
    if (sig.type === 'lake') checks.push("Check the latest CWC monthly glacial-lake monitoring report for this lake's recent change.");
    checks.push(`Check current official flood and river information for ${rv} (CWC and the state flood-control authority). SHAILDHARA provides no flood forecast.`);
    if (ds) checks.push(`Check advisories from the district and state disaster-management authorities for: ${ds}.`);
    checks.push('Treat the exposure figures as "what lies near the river", then verify locally with district authorities before drawing any conclusion.');
    steps.push(stepHtml(7, 'What should be investigated or acted on',
      `<p>${pill('SCENARIO', 'Suggested checks')} <span class="small">Not an official instruction.</span></p><ul>${checks.map(c => `<li>${esc(c)}</li>`).join('')}</ul>`));

    $('#chain-body').innerHTML = steps.join('');
    drawChain(sig, traced, starts, token);
  }

  function setStep(n) { $$('#chain-body .step').forEach(s => s.classList.toggle('on', +s.dataset.step <= n)); }

  // Keep the traced path clear of the chain panel: on wide screens the panel covers the right of the map.
  function fitOpts(maxZoom) {
    const wide = window.matchMedia('(min-width: 901px)').matches;
    return wide ? { paddingTopLeft: [40, 40], paddingBottomRight: [480, 40], maxZoom } : { padding: [24, 24], maxZoom };
  }

  function drawChain(sig, traced, starts, token) {
    // signal location
    const bounds = [];
    if (sig.type === 'lake') {
      L.circleMarker([sig.lat, sig.lon], { radius: 9, color: '#b71c1c', weight: 3, fillColor: '#ffeb3b', fillOpacity: .9 }).addTo(chainGroup);
      bounds.push([sig.lat, sig.lon]);
    } else {
      const dids = sig.type === 'alert' ? sig.alert.districts : [sig.did];
      dids.forEach(d => {
        const dl = districtById[d]; if (!dl) return;
        L.geoJSON(dl.f, { style: { color: '#b71c1c', weight: 2, fillColor: '#b71c1c', fillOpacity: .12 }, interactive: false }).addTo(chainGroup);
        try { const b = dl.lyr.getBounds(); bounds.push(b.getSouthWest(), b.getNorthEast()); } catch (e) { /* ignore */ }
      });
    }
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds), fitOpts(9));
    setStep(2);
    if (!traced) { setStep(7); return; }
    const entry = new Set(starts);
    // animate the path outwards in order of distance from the start
    const order = traced.idx;
    let i = 0;
    const per = Math.max(1, Math.ceil(order.length / 50));
    const poly = [];
    setTimeout(() => {
      if (token !== anim.token) return;
      setStep(3);
      starts.forEach(r => {
        const c = G.coords[r]; if (!c || c.length < 2) return;
        L.polyline(c.map(p => [p[1], p[0]]), { color: '#ff6f00', weight: 5, opacity: .95 }).addTo(chainGroup);
        if (sig.type === 'lake') {   // the lake-to-river gap is proximity only: draw it dashed so it is never mistaken for a confirmed channel
          let best = null, bd = Infinity;
          c.forEach(pt => { const d = C.haversineKm(sig.lat, sig.lon, pt[1], pt[0]); if (d < bd) { bd = d; best = pt; } });
          if (best) L.polyline([[sig.lat, sig.lon], [best[1], best[0]]], { color: '#6d4c9f', weight: 2.5, dashArray: '6 6', opacity: .95, interactive: false }).addTo(chainGroup);
        }
      });
      setTimeout(() => {
        if (token !== anim.token) return;
        setStep(4);
        (function tick() {
          if (token !== anim.token) return;
          for (let k = 0; k < per && i < order.length; k++, i++) {
            const r = order[i], c = G.coords[r];
            if (!c || c.length < 2) continue;
            const ll = c.map(p => [p[1], p[0]]);
            poly.push(...ll);
            L.polyline(ll, { color: entry.has(r) ? '#ff6f00' : '#0b6fa4', weight: entry.has(r) ? 5 : 3.5, opacity: .9 }).addTo(chainGroup);
          }
          if (i < order.length) requestAnimationFrame(() => setTimeout(tick, 30));
          else {
            if (poly.length) map.fitBounds(L.latLngBounds(poly), fitOpts(10));
            setStep(5); setTimeout(() => { if (token === anim.token) { setStep(6); setTimeout(() => { if (token === anim.token) setStep(7); }, 500); } }, 600);
          }
        })();
      }, 700);
    }, 700);
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    $$('.tab').forEach(t => t.onclick = () => {
      $$('.tab').forEach(x => { x.classList.toggle('active', x === t); x.setAttribute('aria-selected', x === t ? 'true' : 'false'); });
      $$('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t.dataset.tab));
    });
    document.addEventListener('click', e => {
      const f = e.target.closest('[data-alert-filter]'); if (f && !f.disabled) { setAlertFilter(f.dataset.alertFilter); return; }
      const b = e.target.closest('[data-chain-alert]'); if (b && !b.disabled) { selectAlert(+b.dataset.chainAlert); return; }
      const lk = e.target.closest('[data-lake]'); if (lk) { $$('.card.sel').forEach(c => c.classList.remove('sel')); lk.classList.add('sel'); selectLake(lk.dataset.lake); return; }
      const h = e.target.closest('[data-hit]');
      if (h) {
        const [t, id] = h.dataset.hit.split('|');
        if (t === 'lake') selectLake(id); else if (t === 'district') selectDistrict(id); else if (t === 'alert') selectAlert(+id);
        else if (t === 'state') { const s = (D.search || []).find(x => x.id === id); if (s) map.setView([s.lat, s.lon], 7); }
      }
    });
    $('#q').addEventListener('input', e => runSearch(e.target.value));
  }

  // One broken part must never blank the whole site: each startup step is isolated and a failure is shown, not hidden.
  const failedParts = [];
  function safely(name, fn) {
    try { fn(); } catch (e) { console.error('SHAILDHARA startup step failed:', name, e); failedParts.push(name); }
  }

  async function main() {
    initMap();
    await loadAll();
    safely('alerts', indexAlerts);
    safely('map layers', buildLayers);
    safely('status bar', renderTop); safely('signals panel', renderSignals); safely('layers panel', renderLayersTab); safely('sources panel', renderSourcesTab); safely('coverage panel', renderCoverageTab);
    safely('controls', wire);
    if (failedParts.length) {
      const b = $('#banner'); b.hidden = false; b.className = 'banner';
      b.innerHTML += ' Part of this page could not load (' + esc(failedParts.join(', ')) + '). The rest still works; see the browser console for details.';
    }
    const kb = $('#keybox'); if (kb) kb.open = window.matchMedia('(min-width: 901px)').matches;
    const ld = $('#loading'); if (ld) ld.hidden = true;
    window.SHAILDHARA = { D, selectDistrict, selectLake, selectAlert, state: () => ({ current, G }), map: () => map, build: '2026-10-12-r4' };   // handy for testing
  }
  main();
})();
