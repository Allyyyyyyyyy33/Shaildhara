"""Coverage audit for SHAILDHARA. Every number is counted from the data files the site actually serves
(web/data/**); nothing is typed in by hand except the reference list of the 28 states and 8 union territories.

Writes web/data/coverage.json. Light: standard library only (shapely is used for station-in-state counts when present).
Run:  python -m pipeline.build_coverage            (also:  --md FILE  to write the human-readable summary)
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"

STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
          "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha",
          "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal"]
UTS = ["Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
       "Ladakh", "Lakshadweep", "Puducherry"]


def norm(s):
    s = (s or "").lower().replace("&", "and").replace("arunanchal", "arunachal").replace("islands", "island").replace("havelli", "haveli")
    return re.sub(r"[^a-z]", "", s)


REF = {norm(n): (n, "State") for n in STATES}
REF.update({norm(n): (n, "Union territory") for n in UTS})


def rj(rel, default=None):
    try:
        return json.loads((DATA / rel).read_text(encoding="utf-8"))
    except Exception:
        return default


def parse(t):
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    except Exception:
        return None


def alert_active(a, now):
    e = parse(a.get("expires"))
    if e:
        return e >= now
    s = parse(a.get("sent"))
    return bool(s and (now - s) <= timedelta(hours=48))


def build(now=None):
    now = now or datetime.now(timezone.utc)
    states = rj("states.geojson", {"features": []})["features"]
    dists = rj("districts.geojson", {"features": []})["features"]
    lakes = rj("glacial_lakes.geojson", {"features": []})["features"]
    net = rj("network/reaches.json", {})
    expo = rj("exposure/reach_exposure.json", {})
    alerts = (rj("live/alerts.json", {}) or {}).get("alerts", [])
    obs = (rj("live/river_observations.json", {}) or {}).get("observations", [])
    live = (rj("live/status.json", {}) or {}).get("sources", {})
    sources = {s.get("dataset_id") or s.get("source_id"): s for s in (rj("sources.json", {}) or {}).get("sources", [])}
    conn = (rj("connectivity_report.json", {}) or {}).get("stats", {})

    rows, by_norm = {}, {}
    for f in states:
        n = f["properties"].get("name")
        ref = REF.get(norm(n))
        rows[n] = dict(name=ref[0] if ref else n, source_name=n, kind=ref[1] if ref else "Unrecognised",
                       districts=0, glacial_lakes=0, river_reaches=0, stations=None, active_alerts=0)
        by_norm[norm(n)] = n
    did_state = {}
    for f in dists:
        p = f["properties"]
        did_state[p.get("did")] = p.get("state")
        if p.get("state") in rows:
            rows[p["state"]]["districts"] += 1
    for f in lakes:
        s = f["properties"].get("state")
        if s in rows:
            rows[s]["glacial_lakes"] += 1
    for di in net.get("district", []):
        try:
            s = net["districts"][di]["s"]
        except Exception:
            continue
        if s in rows:
            rows[s]["river_reaches"] += 1
    # stations -> state (point in polygon) when shapely is available
    try:
        from shapely.geometry import Point, shape
        polys = [(f["properties"].get("name"), shape(f["geometry"])) for f in states]
        for r in rows.values():
            r["stations"] = 0
        for o in obs:
            pt = Point(o["lon"], o["lat"])
            for n, g in polys:
                if g.contains(pt):
                    rows[n]["stations"] += 1
                    break
    except Exception:
        pass
    active = [a for a in alerts if alert_active(a, now)]
    for a in active:
        for s in {did_state.get(d) for d in a.get("districts", [])}:
            if s in rows:
                rows[s]["active_alerts"] += 1

    present = {norm(r["source_name"]) for r in rows.values()}
    missing = [v[0] for k, v in REF.items() if k not in present]
    n_state = sum(1 for r in rows.values() if r["kind"] == "State")
    n_ut = sum(1 for r in rows.values() if r["kind"] == "Union territory")

    def src(i):
        return sources.get(i, {})

    exp_layers = expo.get("layers", {})
    exp_unavail = [u["key"] for u in expo.get("unavailable", [])]
    stations = {(o.get("station"), o.get("lat"), o.get("lon")) for o in obs}
    sa, nw = live.get("sachet", {}), live.get("nwdp_cwc", {})
    linked = sum((conn.get("lake_links") or {}).get(k, 0) for k in ("close_le_1km", "moderate_1_5km", "weak_5_10km"))

    def L(i, label, cat, upd, records, src_id=None, note="", url=None, date=None):
        s = src(src_id) if src_id else {}
        return dict(id=i, label=label, category=cat, update=upd, records=records, source_status=s.get("status"),
                    data_date=date or s.get("data_date"), source_url=url or s.get("url") or s.get("landing_page"), note=note)

    layers = [
        L("admin_state", "State and union-territory boundaries", "AVAILABLE_VERIFIED", "static", len(states), "admin_state",
          "NWIC portal boundary file; analysis outlines, not an official map of India."),
        L("admin_district", "District boundaries", "AVAILABLE_INCOMPLETE", "static", len(dists), "admin_district",
          "Boundary vintage is not stated by the source; districts created after it are not guaranteed to be present, so some alerts cannot be matched."),
        L("river_network", "River network (OpenStreetMap waterways, directed)", "AVAILABLE_INCOMPLETE", "static",
          conn.get("reaches_total"), "osm_india",
          f"{len(net.get('ids', []))} major reaches exported to the browser; {conn.get('districts_with_river_reaches')} of {conn.get('districts_total')} districts have a reach. "
          "Volunteer-mapped: mountain streams are patchy, most reaches are unnamed, and paths can end at mapping gaps."),
        L("glacial_lakes", "Glacial lake inventory (CWC, Himalayan states)", "STATIC_OR_PERIODIC", "static", len(lakes), "glacial_lakes_cwc",
          f"Inventory only, not live monitoring. {sum(1 for r in rows.values() if r['glacial_lakes'])} states/UTs hold lakes; "
          f"{sum(1 for f in lakes if not f['properties'].get('state'))} lakes lie outside the state boundaries (transboundary) and have no state. "
          f"{linked} of {len(lakes)} lakes have a computed link to a river reach (within 10 km)."),
        L("sachet", "NDMA SACHET official alerts (CAP)", "AVAILABLE_VERIFIED" if sa.get("status") in ("OK", "NO_CURRENT_ALERTS") else "BLOCKED",
          "live", len(alerts), "sachet", f"{len(active)} unexpired at build time; district matching by area name (lower confidence) or polygon.",
          date=sa.get("data_date")),
        L("nwdp_telemetry", "CWC / NWDP station readings", "AVAILABLE_INCOMPLETE" if nw.get("status") == "OK" else "BLOCKED", "periodic",
          len(obs), "nwdp_cwc", f"{len(stations)} stations; only datasets with a recognisable schema are read, within a time budget. "
          "Readings are shown as source-reported and are not quality-checked.", date=nw.get("data_date")),
        L("imd_api", "IMD weather API", "BLOCKED", "live", 0, None, "Requires an IMD API key (and possibly IP whitelisting). Not integrated; no IMD data is shown.",
          url="https://api.imd.gov.in/public/register.php"),
        L("exposure_populated", "Exposure along traced rivers (2 km corridor): " + ", ".join(v.get("label", k) for k, v in exp_layers.items()),
          "AVAILABLE_INCOMPLETE", "static", len(exp_layers), "worldpop_india",
          "Population is a 2020 modelled estimate (WorldPop 1 km); OSM counts are crowd-sourced, so a zero can mean 'not mapped'. An indicator, never a damage claim."),
        L("exposure_missing", "Land cover and ecosystems exposure", "BLOCKED", "static", len(exp_unavail), None,
          "No verified automatic download; shown as 'No data', never as zero."),
        L("hydrorivers", "HydroRIVERS (official river network with discharge)", "BLOCKED", "static", 0, "hydrorivers_asia",
          "Automated download is not permitted/blocked by the provider; SHAILDHARA does not work around it."),
        L("cwc_monthly", "CWC monthly glacial-lake reports (PDF)", "PLANNED", "periodic", 0, None, "Published as PDFs; parsing is not implemented."),
        L("basins", "Official river basin / sub-basin boundaries", "PLANNED", "static", 0, None, "No verified programmatic download."),
    ]
    feeds = [dict(id=k, status=v.get("status"), records=v.get("records"), data_date=v.get("data_date"), fetched_at=v.get("fetched_at"),
                  detail=(v.get("detail") or "")[:240]) for k, v in live.items()]
    return dict(
        generated_at=now.isoformat(timespec="seconds"),
        method="Counted from the files served under web/data; reference list of 28 states + 8 union territories is typed in.",
        summary=dict(
            states_and_uts_expected=len(REF), states_represented=n_state, uts_represented=n_ut, missing=missing,
            districts=len(dists), glacial_lakes=len(lakes), river_reaches_total=conn.get("reaches_total"),
            river_reaches_exported=len(net.get("ids", [])), districts_with_river=conn.get("districts_with_river_reaches"),
            alerts_in_file=len(alerts), alerts_unexpired=len(active), alerts_unmatched=sum(1 for a in alerts if not a.get("districts")),
            stations=len(stations), observations=len(obs), exposure_layers_populated=len(exp_layers), exposure_layers_unavailable=exp_unavail,
            live_feeds_ok=[k for k, v in live.items() if v.get("status") in ("OK", "NO_CURRENT_ALERTS")],
            live_feeds_not_working=[k for k, v in live.items() if v.get("status") not in ("OK", "NO_CURRENT_ALERTS")]),
        regions=sorted(rows.values(), key=lambda r: (r["kind"], r["name"])), layers=layers, feeds=feeds,
        honesty=("SHAILDHARA is built as a national platform, but measured coverage is uneven: glacial-lake records cover Himalayan states only, "
                 "live station readings cover only the datasets the portal exposes, IMD data is not integrated, and land-cover/ecosystem exposure is unavailable. "
                 "It is not claimed to be India-wide in operational terms."))


CAT = {"AVAILABLE_VERIFIED": "1. Available and verified", "AVAILABLE_INCOMPLETE": "2. Available but incomplete",
       "STATIC_OR_PERIODIC": "3. Static or periodically updated", "BLOCKED": "4. Blocked or unavailable", "PLANNED": "5. Planned, not implemented"}


def to_markdown(c):
    s = c["summary"]
    out = [f"# SHAILDHARA coverage audit", f"Generated {c['generated_at']} from the files the site serves. {c['honesty']}", "",
           f"- States / UTs represented: **{s['states_represented']} of 28 states, {s['uts_represented']} of 8 union territories**" + (f" (missing: {', '.join(s['missing'])})" if s["missing"] else ""),
           f"- Districts: **{s['districts']}** | Glacial lakes: **{s['glacial_lakes']}** | River reaches in graph: **{s['river_reaches_total']}** ({s['river_reaches_exported']} exported to the browser)",
           f"- Alerts in file: {s['alerts_in_file']} (unexpired at build time: {s['alerts_unexpired']}; without a matched district: {s['alerts_unmatched']})",
           f"- Station readings: {s['observations']} from {s['stations']} stations | Exposure layers populated: {s['exposure_layers_populated']}",
           f"- Live feeds working: {', '.join(s['live_feeds_ok']) or 'none'} | not working: {', '.join(s['live_feeds_not_working']) or 'none'}", ""]
    for k, title in CAT.items():
        rows = [l for l in c["layers"] if l["category"] == k]
        if rows:
            out.append(f"## {title}")
            out += [f"- **{l['label']}** ({l['update']}; records: {l['records']}; data date: {l['data_date'] or 'n/a'}) - {l['note']}" for l in rows]
            out.append("")
    out += ["## Regions", "| Region | Type | Districts | Glacial lakes | River reaches | Stations | Active alerts |", "|---|---|---|---|---|---|---|"]
    out += [f"| {r['name']} | {r['kind']} | {r['districts']} | {r['glacial_lakes']} | {r['river_reaches']} | {r['stations'] if r['stations'] is not None else 'n/a'} | {r['active_alerts']} |" for r in c["regions"]]
    return "\n".join(out) + "\n"


def run():
    c = build()
    (DATA / "coverage.json").write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"coverage: {c['summary']['states_represented']} states + {c['summary']['uts_represented']} UTs, {c['summary']['districts']} districts, "
          f"{len(c['layers'])} layers audited -> web/data/coverage.json")
    return c


if __name__ == "__main__":
    c = run()
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(to_markdown(c), encoding="utf-8")
