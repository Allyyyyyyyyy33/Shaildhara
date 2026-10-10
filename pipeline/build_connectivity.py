"""Stage 3 - river / lake / district connectivity structure.

Downstream links come ONLY from the HydroRIVERS NEXT_DOWN field (dataset topology). CWC river
lines have no documented flow direction, so they are used for river NAMES only.

Outputs (web/data):
  network/reaches.json   compact graph of every reach that lies on at least one anchor path
  anchors.json           district -> entry reaches
  glacial_lakes.geojson  rewritten with the lake -> reach link, snap distance and quality
  connectivity_report.json
Every link is labelled with how it was made (dataset topology vs computed nearest-snap).
"""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from .build_foundation import (STORAGE_CRS, find_vectors, infer_field, NAME_PATTERNS, read_vector, write_geojson)
from .common import INTERIM, WEB_DATA, ensure_dirs, log, save_json, short, utc_iso

PROJ_CRS = "EPSG:7755"   # WGS 84 / India NSF LCC - for distances only
SNAP_MAX_M = 10_000
TOP_K_DISTRICT = 5
MAX_PATH_STEPS = 6000
NAME_SNAP_M = 3000


def _load_cached(name):
    p = INTERIM / "foundation" / f"{name}.pkl"
    return pd.read_pickle(p) if p.exists() else None


def load_hydrorivers(india_geom, bbox):
    vecs = [v for v in find_vectors("hydrorivers_asia") if v.suffix.lower() in (".shp", ".gpkg")]
    if not vecs:
        return None, "HydroRIVERS file not found (download blocked and nothing in data/manual/hydrorivers_asia/)"
    gdf, meta = read_vector(vecs[0], bbox=bbox)
    cols = {c.upper(): c for c in gdf.columns}
    need = ["HYRIV_ID", "NEXT_DOWN"]
    if any(n not in cols for n in need):
        return None, f"HydroRIVERS missing required fields {need}; found {list(gdf.columns)[:12]}"
    keep = gdf.sindex.query(shapely.make_valid(india_geom), predicate="intersects")
    gdf = gdf.iloc[np.sort(np.unique(keep))].reset_index(drop=True)
    rn = {cols["HYRIV_ID"]: "rid", cols["NEXT_DOWN"]: "next_down"}
    for opt, new in (("LENGTH_KM", "len_km"), ("DIS_AV_CMS", "dis"), ("ORD_STRA", "ord"), ("MAIN_RIV", "main_riv"), ("HYBAS_L12", "hybas12")):
        if opt in cols:
            rn[cols[opt]] = new
    gdf = gdf.rename(columns=rn)
    for c in ("len_km", "dis", "ord", "main_riv", "hybas12"):
        if c not in gdf.columns:
            gdf[c] = np.nan
    gdf = gdf[["rid", "next_down", "len_km", "dis", "ord", "main_riv", "hybas12", "geometry"]]
    return gdf, f"{len(gdf)} reaches selected where they touch India (whole reaches kept; {meta['file']})"


def trace(nxt, start):
    path, seen, cur = [], set(), start
    while cur >= 0 and cur not in seen and len(path) < MAX_PATH_STEPS:
        path.append(cur)
        seen.add(cur)
        cur = nxt[cur]
    return path


def run():
    ensure_dirs()
    rep = {"started_at": utc_iso(), "status": "NOT_RUN", "blockers": [], "stats": {}}
    states, dists, lakes = _load_cached("admin_state"), _load_cached("admin_district"), _load_cached("glacial_lakes_cwc")
    if states is None:
        rep.update(status="BLOCKED")
        rep["blockers"].append("State layer missing: cannot define India for selecting river reaches")
        return rep
    india = shapely.union_all(shapely.make_valid(states.geometry.values))
    minx, miny, maxx, maxy = states.total_bounds
    method = "hydrorivers"
    reaches, note = load_hydrorivers(india, (minx - 0.1, miny - 0.1, maxx + 0.1, maxy + 0.1))
    if reaches is None:
        # HydroSHEDS forbids automated download (robots.txt), so fall back to OpenStreetMap waterways (flow direction by OSM convention)
        from .build_osm_network import load_osm_waterways
        osm_reaches, osm_stats, osm_note = load_osm_waterways()
        if osm_reaches is None:
            rep.update(status="BLOCKED")
            rep["blockers"].append(note + "; OpenStreetMap fallback: " + osm_note + ". Downstream tracing is switched off until a river layer exists.")
            log("connectivity: BLOCKED - " + note + " | " + osm_note)
            return rep
        reaches, method = osm_reaches, "osm_waterways"
        rep["stats"]["osm_network"] = osm_stats
        note = osm_note + " [HydroRIVERS unavailable: " + note[:120] + "]"
    rep["stats"]["river_source"] = method
    for c in ("acc", "osm_name"):
        if c not in reaches.columns:
            reaches[c] = np.nan if c == "acc" else None
    log("connectivity: " + note)

    n = len(reaches)
    rid = reaches["rid"].astype("int64").values
    index_of = {int(v): i for i, v in enumerate(rid)}
    nd = reaches["next_down"].fillna(0).astype("int64").values
    nxt = np.full(n, -1, dtype="int64")
    end_code = np.zeros(n, dtype="int8")  # 0 continues, 1 terminal (sea/sink), 2 leaves the India selection
    for i, d in enumerate(nd):
        if d == 0:
            end_code[i] = 1
        elif int(d) in index_of:
            nxt[i] = index_of[int(d)]
        else:
            end_code[i] = 2
    rep["stats"].update(reaches_total=int(n), reaches_terminal=int((end_code == 1).sum()), reaches_leaving_selection=int((end_code == 2).sum()))

    reaches_p = reaches.to_crs(PROJ_CRS)
    anchors_lakes, link_stats = {}, {"close_le_1km": 0, "moderate_1_5km": 0, "weak_5_10km": 0, "unlinked": 0}

    # ---- glacial lake anchors: nearest reach within SNAP_MAX_M (computed, labelled)
    if lakes is not None and len(lakes):
        lp = lakes.copy()
        lp = gpd.GeoDataFrame(lp[["lid"]], geometry=lakes.geometry.values, crs=STORAGE_CRS).to_crs(PROJ_CRS)
        j = gpd.sjoin_nearest(lp, reaches_p[["rid", "geometry"]].reset_index().rename(columns={"index": "ri"}),
                              how="left", max_distance=SNAP_MAX_M, distance_col="snap_m")
        j = j.sort_values("snap_m").drop_duplicates("lid", keep="first")
        for _, r in j.iterrows():
            if pd.isna(r.get("ri")):
                anchors_lakes[r["lid"]] = None
                link_stats["unlinked"] += 1
                continue
            km = float(r["snap_m"]) / 1000.0
            q = "close" if km <= 1 else ("moderate" if km <= 5 else "weak")
            link_stats[{"close": "close_le_1km", "moderate": "moderate_1_5km", "weak": "weak_5_10km"}[q]] += 1
            anchors_lakes[r["lid"]] = (int(r["ri"]), round(km, 2), q)
    # ---- district anchors: largest reaches (modelled discharge, else stream order) intersecting each district
    district_entry = {}
    if dists is not None:
        sidx = reaches.sindex
        score = (reaches["dis"].fillna(0).values if method == "hydrorivers" else reaches["acc"].fillna(0).values) + reaches["ord"].fillna(0).values * 1e-6
        for _, d in dists.iterrows():
            hit = sidx.query(shapely.make_valid(d.geometry), predicate="intersects")
            if len(hit):
                top = hit[np.argsort(-score[hit])[:TOP_K_DISTRICT]]
                district_entry[d["did"]] = [int(t) for t in top]
        rep["stats"]["districts_with_river_reaches"] = len(district_entry)
        rep["stats"]["districts_total"] = int(len(dists))
    else:
        rep["blockers"].append("District layer missing: district-based (alert) chains unavailable")

    # ---- union of all anchor paths -> compact exported network
    used = set()
    starts = [a[0] for a in anchors_lakes.values() if a] + [i for v in district_entry.values() for i in v]
    ends = {"terminal": 0, "leaves_selection": 0, "loop_or_limit": 0}
    for s in set(starts):
        p = trace(nxt, s)
        used.update(p)
        last = p[-1]
        if end_code[last] == 1:
            ends["terminal"] += 1
        elif end_code[last] == 2:
            ends["leaves_selection"] += 1
        else:
            ends["loop_or_limit"] += 1
    rep["stats"]["distinct_anchor_start_reaches"] = len(set(starts))
    rep["stats"]["path_end_reasons"] = ends
    order = sorted(used)
    new_index = {old: k for k, old in enumerate(order)}
    sub = reaches.iloc[order].reset_index(drop=True)

    # district / state of each exported reach (reach midpoint - approximate, labelled)
    d_of = np.full(len(sub), -1, dtype="int32")
    dlist = []
    if dists is not None:
        dlist = [{"id": r["did"], "n": r["name"], "s": r["state"], "sid": r["sid"]} for _, r in dists.iterrows()]
        mids = shapely.line_interpolate_point(sub.geometry.values, 0.5, normalized=True)
        qi, ti = dists.sindex.query(mids, predicate="within")
        for a, b in zip(qi, ti):
            if d_of[int(a)] < 0:
                d_of[int(a)] = int(b)

    # river names from the nearest CWC river line within NAME_SNAP_M (computed, labelled)
    names_idx = [-1] * len(sub)
    name_list = []
    rv = _load_cached("river_network_cwc")
    if rv is not None and len(rv):
        nf = infer_field([c for c in rv.columns if c != "geometry" and not c.startswith("shd_")], NAME_PATTERNS["river"])
        if nf:
            try:
                rvp = rv[[nf, "geometry"]].dropna(subset=[nf]).to_crs(PROJ_CRS)
                subp = sub[["geometry"]].to_crs(PROJ_CRS)
                subp["ri"] = np.arange(len(subp))
                jn = gpd.sjoin_nearest(subp, rvp, how="left", max_distance=NAME_SNAP_M, distance_col="dm")
                jn = jn.sort_values("dm").drop_duplicates("ri", keep="first")
                lookup = {}
                for _, r in jn.iterrows():
                    if pd.notna(r.get(nf)) and str(r[nf]).strip() and not str(r[nf]).strip().replace(".", "", 1).isdigit():
                        nm = str(r[nf]).strip()          # purely numeric values are codes, not river names
                        if nm not in lookup:
                            lookup[nm] = len(name_list)
                            name_list.append(nm)
                        names_idx[int(r["ri"])] = lookup[nm]
                rep["stats"]["reaches_with_cwc_name"] = int(sum(1 for x in names_idx if x >= 0))
            except Exception as e:
                rep["blockers"].append(f"River naming skipped: {short(e)}")
        else:
            rep["blockers"].append("CWC river layer has no recognisable name field; reaches stay unnamed")
    else:
        rep["blockers"].append("CWC river layer missing: reaches stay unnamed")

    if method == "osm_waterways":
        lookup = {n: i for i, n in enumerate(name_list)}
        added = 0
        for i, nm in enumerate(sub["osm_name"].values):
            if names_idx[i] < 0 and isinstance(nm, str) and nm.strip():
                nm = nm.strip()
                if nm not in lookup:
                    lookup[nm] = len(name_list)
                    name_list.append(nm)
                names_idx[i] = lookup[nm]
                added += 1
        rep["stats"]["reaches_with_osm_name"] = added

    coords = []
    simp = sub.geometry.simplify(0.002, preserve_topology=False)
    for g in simp.values:
        if g is None or g.is_empty:
            coords.append([])
            continue
        parts = [g] if g.geom_type == "LineString" else list(g.geoms)
        line = max(parts, key=lambda x: x.length)
        coords.append([[round(x, 4), round(y, 4)] for x, y in line.coords])
    net = {
        "generated_at": utc_iso(),
        "method": method,
        "source": ("HydroRIVERS v1.0 (HydroSHEDS) - dataset topology via NEXT_DOWN; modelled discharge" if method == "hydrorivers" else
                   "OpenStreetMap waterways (c) OpenStreetMap contributors, ODbL, via Geofabrik - downstream links computed from way direction"),
        "source_label": ("HydroRIVERS (international dataset topology)" if method == "hydrorivers" else
                         "OpenStreetMap waterways: links computed from the drawing direction of each waterway (OSM convention), not hydrologically modelled"),
        "rank_label": "largest by modelled discharge" if method == "hydrorivers" else "most mapped reaches upstream (computed; no discharge available)",
        "caveat": ("small streams are missing at this resolution" if method == "hydrorivers" else
                   "mapping is volunteer-made and uneven in mountains; a waterway drawn backwards or a gap breaks the link"),
        "terminal_text": ("reaches the end of the network (sea or inland sink)" if method == "hydrorivers" else
                          "reaches the end of the MAPPED waterway (sea, inland sink, or a gap in the mapping)"),
        "acc": [None if pd.isna(x) else int(x) for x in sub["acc"].values],
        "ids": [int(x) for x in sub["rid"].values],
        "next": [int(new_index.get(int(o), -1)) if o >= 0 else -1 for o in nxt[order]],
        "end": [int(x) for x in end_code[order]],
        "len_km": [None if pd.isna(x) else round(float(x), 2) for x in sub["len_km"].values],
        "ord": [None if pd.isna(x) else int(x) for x in sub["ord"].values],
        "dis_cms": [None if pd.isna(x) else round(float(x), 3) for x in sub["dis"].values],
        "district": [int(x) for x in d_of],
        "name": names_idx,
        "coords": coords,
        "districts": dlist,
        "names": name_list,
    }
    save_json(WEB_DATA / "network" / "reaches.json", net)

    anchors = {"generated_at": utc_iso(), "method": "district entry reaches = the largest reaches (modelled discharge) intersecting the district",
               "top_k": TOP_K_DISTRICT, "districts": {k: [new_index[i] for i in v if i in new_index] for k, v in district_entry.items()}}
    save_json(WEB_DATA / "anchors.json", anchors)

    # rewrite lakes with link info
    if lakes is not None:
        lk = lakes.copy()
        lk["reach"] = pd.Series([(new_index.get(anchors_lakes.get(l)[0]) if anchors_lakes.get(l) else None) for l in lk["lid"]],
                                index=lk.index, dtype=object)
        lk["snap_km"] = pd.Series([anchors_lakes[l][1] if anchors_lakes.get(l) else None for l in lk["lid"]], index=lk.index, dtype=object)
        lk["quality"] = [anchors_lakes[l][2] if anchors_lakes.get(l) else "unlinked" for l in lk["lid"]]
        write_geojson(WEB_DATA / "glacial_lakes.geojson", lk,
                      ["lid", "name", "area_ha_calc", "area_ha_src", "did", "state", "shd_src_id", "reach", "snap_km", "quality"], nd=4)
    rep["stats"]["lake_links"] = link_stats
    rep["stats"]["exported_reaches"] = int(len(sub))
    rep["status"] = "OK"
    rep["finished_at"] = utc_iso()
    save_json(WEB_DATA / "connectivity_report.json", rep, compact=False)
    log(f"connectivity: OK - {len(sub)} reaches exported; lake links {link_stats}")
    return rep
