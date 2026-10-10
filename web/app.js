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
      live: 'data/live/status.json', obs: 'data/live/river_observations.json', nwdpcat: 'data/live/nwdp_catalogue.json',
    };
    await Promise.all(Object.entries(paths).map(async ([k, p]) => { D[k] = await getJSON(p); }));
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
  function indexAlerts() {
    sevByDistrict = {}; alertsByDistrict = {};
    alertList().forEach(a => (a.districts || []).forEach(d => {
      (alertsByDistrict[d] = alertsByDistrict[d] || []).push(a);
      sevByDistrict[d] = Math.max(sevByDistrict[d] || 0, C.severityRank(a.severity));
    }));
  }

  // ---------------------------------------------------------------- map
  function initMap() {
    map = L.map('map', { center: [23.5, 80], zoom: 5, minZoom: 4, preferCanvas: true, zoomControl: true });
    base = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 14, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    chainGroup = L.layerGroup().addTo(map);
    layers.base = base;
    map.attributionControl.addAttribution('SHAILDHARA: data from the sources listed under "Sources"');
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
        L.circleMarker([o.lat, o.lon], { radius: 5, color: '#1b5e20', fillColor: '#66bb6a', fillOpacity: .9, weight: 1 })
          .bindTooltip(`${esc(o.station)}: ${esc(o.parameter)} ${esc(o.value)} (${esc(fmt(o.observed_at))}) - as reported by the source, not quality-checked by SHAILDHARA`).addTo(obsLayer);
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
    const bits = [];
    if (D.build) bits.push('Data built: ' + fmt(D.build.generated_at));
    const al = D.live && D.live.sources && D.live.sources.sachet;
    bits.push(D.alerts ? 'Alerts: ' + alertList().length + ' current (feed refreshed ' + fmt(D.alerts.generated_at) + ')' : 'Alerts: ' + (al ? esc(al.status) : 'not loaded'));
    $('#top-status').innerHTML = bits.join(' &nbsp;|&nbsp; ');
    $('#legend').innerHTML = ['OFFICIAL', 'CURRENT', 'LATEST', 'COMPUTED', 'SCENARIO', 'NODATA'].map(k => pill(k)).join('');
  }

  // ---------------------------------------------------------------- signals tab
  function alertCard(a, i) {
    const sev = C.severityRank(a.severity);
    const entry = G && D.anchors ? C.entryReachesForDistricts(G, D.anchors, a.districts, 40) : [];
    const can = entry.length > 0;
    const why = !a.districts.length ? 'No district could be matched to this alert, so no river connection can be computed.'
      : !G ? 'River network not built yet (see Sources).' : (!can ? 'No river reaches found in the alerted districts.' : '');
    const status = C.alertActive(a) ? 'CURRENT' : 'STALE';
    return `<div class="card" data-alert="${i}">
      <h3>${esc(a.event || a.headline || 'Alert')}</h3>
      <div>${pill('OFFICIAL')}${status === 'CURRENT' || !a.expires ? pill('CURRENT') : ''}<span class="pill sev sev-${sev}">${esc(a.severity || 'severity not given')}</span></div>
      <div class="meta">${esc(a.sender || 'sender not given')} &middot; issued ${fmt(a.sent)}${a.expires ? ' &middot; expires ' + fmt(a.expires) : ''}</div>
      <div class="meta">${esc((a.areas || []).slice(0, 3).join('; '))}${(a.areas || []).length > 3 ? ' ...' : ''}</div>
      <div class="meta">Districts matched: ${a.districts.length} (${a.match_method === 'POLYGON' ? 'by alert polygon' : (a.match_method === 'NAME_MATCH' ? 'by area name, lower confidence' : esc(a.match_method))})</div>
      <button class="chain-btn" data-chain-alert="${i}" ${can ? '' : 'disabled'}>SHOW ME THE CHAIN</button>
      ${why ? `<div class="reason">${esc(why)}</div>` : ''}
      ${prov({ source: 'NDMA SACHET (' + (a.sender || 'issuing agency') + ')', kind: 'WARNING (official, as issued)', dataDate: a.sent, fetchedAt: D.alerts.generated_at, status: 'CURRENT' })}
    </div>`;
  }

  function sourceStatusCards() {
    const src = (D.live && D.live.sources) || {};
    const keys = Object.keys(src);
    if (!keys.length) return `<div class="card">${pill('NODATA')} <div class="small">No live-source status file found. Run the pipeline's live stage (<code>python -m pipeline.build_all --live</code>).</div></div>`;
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
    let h = '<h2>Current official alerts</h2>';
    if (!D.alerts) h += `<div class="card">${pill('NODATA')}<div class="small">No alert file found. Run the live stage of the pipeline.</div></div>`;
    else if (!al.length) h += `<div class="card">${pill('NODATA')}<div class="small">No unexpired alerts in the feeds read at ${fmt(D.alerts.generated_at)}${D.alerts.expired_since_refresh ? ' (' + D.alerts.expired_since_refresh + ' alert(s) in that file have since expired and are hidden)' : ''}. This can be genuinely quiet, or the feeds may not be reachable: see the status cards below.</div></div>`;
    else {
      if (D.alerts.expired_since_refresh) h += `<div class="small">${D.alerts.expired_since_refresh} alert(s) from the ${fmt(D.alerts.generated_at)} refresh have expired and are hidden.</div>`;
      const order = al.map((a, i) => i).sort((x, y) => C.severityRank(al[y].severity) - C.severityRank(al[x].severity));
      h += order.slice(0, 80).map(i => alertCard(al[i], i)).join('');
      if (order.length > 80) h += `<div class="small">Showing the 80 highest-severity alerts of ${order.length}.</div>`;
    }
    h += '<h2>Live source status</h2>' + sourceStatusCards();
    h += '<h2>Mountain inventory: glacial lakes</h2>';
    if (!D.lakes) h += `<div class="card">${pill('NODATA')}<div class="small">Glacial-lake layer not built yet.</div></div>`;
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
        <div class="meta">River link: ${esc(p.quality || 'not computed')}${p.snap_km != null ? ' (' + p.snap_km + ' km to nearest river reach)' : ''}</div></div>`;
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
      h += `<label class="layer-row"><input type="checkbox" data-layer="${k}" ${on && avail ? 'checked' : ''} ${avail ? '' : 'disabled'}> ${esc(label)} ${avail ? '' : pill('NODATA')}</label>`;
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
  function renderSourcesTab() {
    let h = '<h2>Build status and blockers</h2>';
    if (!D.build) h += `<div class="card">${pill('NODATA')}<div class="small">No build report found. Run the pipeline.</div></div>`;
    else {
      h += `<div class="small">Built ${fmt(D.build.generated_at)}</div>`;
      const bl = D.build.blockers || [];
      h += bl.length ? '<ul>' + bl.map(b => `<li class="small warn">${esc(b)}</li>`).join('') + '</ul>' : '<div class="small">No blockers recorded.</div>';
      if ((D.build.actions || []).length) h += '<h2>Actions needed from the project owner</h2><ul>' + D.build.actions.map(a => `<li class="small">${esc(a)}</li>`).join('') + '</ul>';
    }
    if (D.conn && D.conn.stats) {
      const s = D.conn.stats;
      h += `<h2>River connectivity</h2><div class="small">Reaches exported: ${esc(s.exported_reaches)}; lake links: ${esc(JSON.stringify(s.lake_links || {}))}; path ends: ${esc(JSON.stringify(s.path_end_reasons || {}))}. Downstream links come from ${esc((D.net && D.net.source_label) || 'HydroRIVERS (international dataset topology)')}.</div>`;
    }
    if (D.audit && D.audit.layers) {
      h += '<h2>Data audit</h2><table><tr><th>Layer</th><th>Features</th><th>Invalid</th><th>Dup IDs</th></tr>' +
        D.audit.layers.map(a => `<tr><td>${esc(a.layer)}</td><td>${esc(a.features)}</td><td>${esc(a.invalid_geometries)}</td><td>${esc(a.duplicate_id_rows)}</td></tr>`).join('') + '</table>';
    }
    h += '<h2>Datasets and feeds</h2>';
    const rows = (D.sources && D.sources.sources) || [];
    if (!rows.length) h += `<div class="card">${pill('NODATA')}<div class="small">No provenance records yet.</div></div>`;
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
      const r = sig.props.reach;
      return r == null ? { starts: [], note: 'This lake has no river reach within 10 km in the network dataset, so no connection is computed.' } : { starts: [r] };
    }
    const dids = sig.type === 'alert' ? sig.alert.districts : [sig.did];
    const starts = D.anchors ? C.entryReachesForDistricts(G, D.anchors, dids, 40) : [];
    return starts.length ? { starts } : { starts: [], note: 'No river reaches in the network dataset intersect these districts.' };
  }

  function closeChain() { anim.token++; $('#chainpanel').hidden = true; chainGroup.clearLayers(); current = null; $$('.card.sel').forEach(c => c.classList.remove('sel')); }

  function openChain(sig) {
    anim.token++;
    const panel = $('#chainpanel');
    panel.hidden = false;
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
        ${prov({ source: 'NDMA SACHET / ' + (a.sender || 'issuing agency'), kind: 'WARNING (as issued)', dataDate: a.sent, fetchedAt: D.alerts && D.alerts.generated_at, status: 'CURRENT' })}`;
    } else if (sig.type === 'lake') {
      const p = sig.props, si = sourceInfo('glacial_lakes_cwc');
      s1 = `<p><strong>${esc(p.name || 'Glacial lake ' + p.lid)}</strong>${p.state ? ', ' + esc(p.state) : ''}. Outline area ${p.area_ha_calc != null ? (+p.area_ha_calc).toFixed(1) + ' ha (calculated)' : 'unknown'}.</p>
        <p>${pill('LATEST', 'Latest official update')}${pill('NODATA', 'No current data: lake change')}</p>
        <p class="small">This is a location from the CWC glacial-lake inventory. Current lake change is only published in CWC's monthly PDF reports, which are not machine-readable, so no current change is shown.</p>
        ${prov({ source: 'CWC glacial lakes via NWDP', kind: 'STATIC inventory', dataDate: si && si.data_date, fetchedAt: si && si.retrieved_at, status: 'LATEST OFFICIAL' })}`;
    } else {
      const al = alertsByDistrict[sig.did] || [];
      s1 = al.length ? `<p>${al.length} current official alert(s) cover this district:</p><ul>${al.slice(0, 5).map(a => `<li>${esc(a.event || 'Alert')} (${esc(a.severity || '?')}, ${esc(a.sender || 'agency')})</li>`).join('')}</ul><p>${pill('OFFICIAL')}${pill('CURRENT')}</p>`
        : `<p>${pill('NODATA', 'No current alert')} No unexpired official alert in the loaded feeds matches this district.</p><p class="small">The chain below shows connected rivers only. It is a map exercise, not a response to an event.</p>`;
    }
    steps.push(stepHtml(1, 'What is changing', s1, true));

    // 2. trigger
    let s2;
    if (sig.type === 'alert') {
      const a = sig.alert;
      s2 = `<p>${pill('OFFICIAL')} The agency's own wording (SHAILDHARA does not model what this event triggers):</p>
        <p class="small">${esc(a.description || a.headline || 'No description text in the alert.')}</p>${a.instruction ? `<p class="small">Instruction as issued: ${esc(a.instruction)}</p>` : ''}
        ${safeUrl(a.cap_url) ? `<p class="small"><a href="${esc(safeUrl(a.cap_url))}" target="_blank" rel="noopener">Original alert (CAP)</a></p>` : ''}`;
    } else s2 = `<p>${pill('NODATA')} No current trigger information is available for this ${sig.type === 'lake' ? 'lake' : 'district'}. SHAILDHARA does not infer triggers.</p>`;
    steps.push(stepHtml(2, 'What it could trigger', s2));

    // 3. water connection
    let s3;
    if (!traced) s3 = `<p>${pill('NODATA')} ${esc(note || 'No connection could be computed.')}</p>`;
    else if (sig.type === 'lake') {
      const p = sig.props;
      const q = { close: 'within 1 km', moderate: '1 to 5 km away', weak: '5 to 10 km away (weak link)' }[p.quality] || esc(p.quality);
      s3 = `<p>${pill('COMPUTED')} Nearest river reach in the network dataset: <strong>${q}</strong> (${esc(p.snap_km)} km).</p>
        <p class="small">The link is a nearest-reach match from map geometry. Rivers: ${sum.rivers.length ? esc(sum.rivers.slice(0, 4).join(', ')) : 'unnamed in the dataset'}.</p>`;
    } else {
      s3 = `<p>${pill('COMPUTED')} ${starts.length} large river reach(es) intersect the district${sig.type === 'alert' ? 's covered by the alert' : ''} (${esc((D.net && D.net.rank_label) || 'largest by modelled discharge')}).</p>
        <p class="small">Rivers: ${sum.rivers.length ? esc(sum.rivers.slice(0, 6).join(', ')) : 'unnamed in the dataset'}. Reaches come from ${esc((D.net && D.net.method === 'osm_waterways') ? 'OpenStreetMap waterways' : 'HydroRIVERS')}; ${esc((D.net && D.net.caveat) || 'small streams are missing at this resolution')}.</p>`;
    }
    steps.push(stepHtml(3, 'Water / river / lake connection', s3));

    // 4. downstream pathway
    let s4;
    if (!traced) s4 = `<p>${pill('NODATA')} No pathway.</p>`;
    else {
      const reasons = traced.reasons;
      const endText = reasons.terminal ? ((D.net && D.net.terminal_text) || 'reaches the end of the network (sea or inland sink)') : '';
      const leave = reasons.leaves_selection ? 'leaves the part of the dataset that touches India (continues outside it; transboundary rivers are not followed)' : '';
      const hz = reasons.horizon ? `stopped at the ${maxKm} km display horizon` : '';
      s4 = `<p>${pill('COMPUTED')}${maxKm ? pill('SCENARIO', 'Scenario horizon ' + maxKm + ' km') : ''}</p>
        <p>${Math.round(traced.totalKm).toLocaleString()} km of river reaches shown, ${sum.districts.length} district(s), ${new Set(sum.states).size} state(s). Path ${[endText, leave, hz].filter(Boolean).join('; ') || 'ends for an unrecorded reason'}.</p>
        <p class="small">Districts downstream (order of first contact, by reach midpoint, approximate): ${esc(sum.districts.slice(0, 14).map(d => d.n + (d.s ? ' (' + d.s + ')' : '')).join(' > ')) || 'none mapped'}${sum.districts.length > 14 ? ' ...' : ''}</p>
        <p class="small">This is where the water network connects to, not where an event will go.</p>`;
      const obs = (D.obs && D.obs.observations) || [];
      if (obs.length) {
        const near = C.stationsNearPath(G, traced.idx, obs, 5).slice(0, 8);
        s4 += near.length ? `<p class="small">${pill('OFFICIAL')} Official observations within 5 km of the path:</p><ul>${near.map(o => `<li>${esc(o.station)}: ${esc(o.parameter)} ${esc(o.value)} at ${fmt(o.observed_at)} ${C.freshness(o.observed_at, 24) === 'CURRENT' ? pill('CURRENT') : pill('LATEST', 'Older than 24 h')}</li>`).join('')}</ul>`
          : '<p class="small">No loaded official station lies within 5 km of this path.</p>';
      } else s4 += `<p class="small">${pill('NODATA', 'No current river observations')} No NWDP/CWC station observations were extracted (see Sources).</p>`;
    }
    steps.push(stepHtml(4, 'Downstream pathway', s4));

    // 5. exposure
    let s5;
    if (!traced) s5 = `<p>${pill('NODATA')} No path, so no exposure is computed.</p>`;
    else {
      const rows = expo.map(e => `<tr><td>${esc(e.label || e.key)}</td><td>${e.total == null ? pill('NODATA') : esc(Math.round(e.total).toLocaleString()) + ' ' + esc(e.unit || '')}</td><td>${e.covered}/${e.of}</td></tr>`).join('');
      const un = ((D.expo && D.expo.unavailable) || []).map(u => `<li class="small">${esc(u.key)}: ${esc(u.reason)}</li>`).join('');
      s5 = `<p>${pill('COMPUTED')} Assets lying within ${D.expo ? D.expo.corridor_m / 1000 : 2} km of the traced river reaches (spatial association only).</p>
        ${rows ? `<table><tr><th>Layer</th><th>Total</th><th>Reaches covered</th></tr>${rows}</table>` : `<p>${pill('NODATA')} No exposure layer has been built yet.</p>`}
        ${un ? `<p class="small">Not available:</p><ul>${un}</ul>` : ''}
        <p class="small">Missing coverage is shown as No current data, never as zero. Population is modelled, OpenStreetMap is crowd-sourced, and land cover depends on the tiles supplied. ${expo.length ? '' : ''}</p>
        ${expo.map(e => `<div class="prov">${esc(e.label)}: ${esc(e.dataset || '')}. ${esc(e.caveats || '')}</div>`).join('')}`;
    }
    steps.push(stepHtml(5, 'Current exposure along the path', s5));

    // 6. consequences
    steps.push(stepHtml(6, 'Potential economic / ecological consequences',
      `<p>${pill('NODATA', 'No current data: not quantified')}</p>
       <p class="small">SHAILDHARA does not estimate economic or ecological loss. No reliable current source exists for it. The exposure above lists what is located near the river. Whether anything is affected depends on the real event and must be judged by the responsible authorities.</p>`));

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
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40], maxZoom: 9 });
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
      starts.forEach(r => { const c = G.coords[r]; if (c && c.length > 1) L.polyline(c.map(p => [p[1], p[0]]), { color: '#ff6f00', weight: 5, opacity: .95 }).addTo(chainGroup); });
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
            if (poly.length) map.fitBounds(L.latLngBounds(poly), { padding: [40, 40], maxZoom: 10 });
            setStep(5); setTimeout(() => { if (token === anim.token) { setStep(6); setTimeout(() => { if (token === anim.token) setStep(7); }, 500); } }, 600);
          }
        })();
      }, 700);
    }, 700);
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    $$('.tab').forEach(t => t.onclick = () => {
      $$('.tab').forEach(x => x.classList.toggle('active', x === t));
      $$('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t.dataset.tab));
    });
    document.addEventListener('click', e => {
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
    safely('status bar', renderTop); safely('signals panel', renderSignals); safely('layers panel', renderLayersTab); safely('sources panel', renderSourcesTab);
    safely('controls', wire);
    if (failedParts.length) {
      const b = $('#banner'); b.hidden = false; b.className = 'banner';
      b.innerHTML += ' Part of this page could not load (' + esc(failedParts.join(', ')) + '). The rest still works; see the browser console for details.';
    }
    window.SHAILDHARA = { D, selectDistrict, selectLake, selectAlert, state: () => ({ current, G }) };   // handy for testing
  }
  main();
})();
