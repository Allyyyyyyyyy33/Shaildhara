"""Stage 2 - national geographic foundation.

Reads the raw files (never modifies them), converts everything to EPSG:4326, keeps original
identifiers and fields, audits quality, and writes web-ready layers:
  web/data/states.geojson, districts.geojson, glacial_lakes.geojson, search_index.json, audit.json
"""
import json
import re
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
pd_NA = pd.NA
import shapely
from shapely.geometry import mapping
from shapely.validation import explain_validity

from .common import (INTERIM, MANUAL, PROCESSED, RAW, WEB_DATA, ensure_dirs, log, save_json, short, utc_iso)

STORAGE_CRS = "EPSG:4326"
AREA_CRS = "EPSG:6933"


# --------------------------------------------------------------------------- reading
def _safe_extract(zip_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not str((out_dir / name).resolve()).startswith(str(root)):
                raise ValueError(f"Unsafe path in ZIP: {name}")
        z.extractall(out_dir)
    return out_dir


def find_vectors(ds_id):
    """Vector files for a dataset (manual files win over raw downloads). ZIPs are extracted to a copy."""
    found = []
    for base in (MANUAL / ds_id, RAW / ds_id):
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if "_extracted" in p.parts or not p.is_file():
                continue
            if p.suffix.lower() == ".zip":
                out = _safe_extract(p, INTERIM / ds_id / p.stem)
                for ext in ("*.geojson", "*.json", "*.shp", "*.gpkg"):
                    found += sorted(out.rglob(ext))
            elif p.suffix.lower() in (".geojson", ".json", ".shp", ".gpkg"):
                found.append(p)
        if found:
            break
    return found


def read_vector(path, bbox=None):
    gdf = gpd.read_file(path, bbox=bbox, engine="pyogrio") if bbox else gpd.read_file(path, engine="pyogrio")
    meta = {"file": str(path), "rows_read": int(len(gdf)), "notes": []}
    if gdf.crs is None:
        if str(path).lower().endswith((".geojson", ".json")):
            gdf = gdf.set_crs(STORAGE_CRS)
            meta["crs_original"] = "NONE IN FILE (EPSG:4326 assumed per GeoJSON standard)"
            meta["notes"].append("GeoJSON without CRS: EPSG:4326 assumed per the GeoJSON standard")
        else:
            raise ValueError("No CRS in file and not GeoJSON - refusing to guess")
    else:
        meta["crs_original"] = gdf.crs.to_string()
    return gdf.to_crs(STORAGE_CRS).reset_index(drop=True), meta


# --------------------------------------------------------------------------- standardising
def infer_field(columns, patterns):
    low = {c.lower(): c for c in columns}
    for pat in patterns:
        for lc, orig in low.items():
            if re.search(pat, lc):
                return orig
    return None


NAME_PATTERNS = {
    "state": [r"^st(ate)?_?(nm|name)$", r"^state$", r"stname", r"^st_nm", r"state.*name", r"^name$"],
    "district": [r"^d(is)?t(rict)?_?(nm|name)$", r"^district$", r"dtname", r"district.*name", r"^name$"],
    "lake": [r"lake.*name", r"^name$", r"^lake$", r"name"],
    "area": [r"area.*ha", r"^area$", r"water.*area", r"area"],
    "river": [r"river.*name", r"^riv.*nm", r"^name$", r"river", r"name"],
}


def standardise(gdf, layer_id):
    """Add shd_ columns (source row, original id, validity). Original columns are kept unchanged."""
    cols = [c for c in gdf.columns if c != "geometry"]
    gdf = gdf.copy()
    gdf["shd_src_row"] = np.arange(len(gdf))
    id_field, best = None, -1.0
    for c in cols:
        if re.search(r"(id|code|cd)$|^(id|code|cd|fid|gid|objectid)", c.lower()) and gdf[c].notna().all():
            ratio = gdf[c].nunique() / max(len(gdf), 1)
            if ratio > best:
                id_field, best = c, ratio
    gdf["shd_src_id"] = gdf[id_field].astype(str) if id_field else gdf["shd_src_row"].astype(str)
    geom = gdf.geometry
    gdf["shd_geom_empty"] = (geom.isna() | geom.is_empty).values
    gdf["shd_geom_valid"] = geom.is_valid.values
    gdf["shd_layer"] = layer_id
    return gdf, {"id_field": id_field or "(row number)", "id_uniqueness": round(best, 4) if id_field else None}


def audit(gdf, layer_id, meta, idinfo):
    valid = gdf.loc[~gdf["shd_geom_valid"] & ~gdf["shd_geom_empty"]]
    nulls = []
    for c in gdf.columns:
        if c == "geometry" or c.startswith("shd_"):
            continue
        s = gdf[c]
        miss = int(s.isna().sum()) + (int((s.astype(str).str.strip() == "").sum()) if s.dtype == object else 0)
        nulls.append({"field": c, "missing_pct": round(100 * miss / max(len(gdf), 1), 1)})
    dup = gdf["shd_src_id"].duplicated(keep=False)
    b = [round(float(x), 4) for x in gdf.total_bounds]
    out = {"layer": layer_id, "features": int(len(gdf)), "crs_original": meta.get("crs_original"), "crs_final": STORAGE_CRS,
           "extent": b, "geometry_types": gdf.geom_type.value_counts().to_dict(),
           "invalid_geometries": int(len(valid)),
           "invalid_examples": [explain_validity(g) for g in valid.geometry.head(3)],
           "empty_geometries": int(gdf["shd_geom_empty"].sum()),
           "id_field": idinfo["id_field"], "duplicate_id_rows": int(dup.sum()),
           "fields_over_50pct_missing": [n["field"] for n in nulls if n["missing_pct"] >= 50], "notes": meta.get("notes", [])}
    proj = gdf.loc[~gdf["shd_geom_empty"]].to_crs(AREA_CRS)
    if len(proj) and proj.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        out["total_area_km2"] = round(float(proj.area.sum() / 1e6), 1)
    if len(proj) and proj.geom_type.isin(["LineString", "MultiLineString"]).all():
        out["total_length_km"] = round(float(proj.length.sum() / 1e3), 1)
    return out


# --------------------------------------------------------------------------- web writers
def _round_coords(obj, nd):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(x), nd) for x in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def write_geojson(path, gdf, props_cols, nd=4):
    feats = []
    for _, r in gdf.iterrows():
        if r.geometry is None or r.geometry.is_empty:
            continue
        g = mapping(r.geometry)
        g["coordinates"] = _round_coords(g["coordinates"], nd)
        props = {}
        for c in props_cols:
            v = r[c]
            if isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.floating, float)):
                v = None if (v != v or v in (float("inf"), float("-inf"))) else round(float(v), 3)
            elif v is pd_NA or (v is not None and not isinstance(v, (str, int, float, bool)) and str(v) in ("<NA>", "NaT", "nan")):
                v = None
            elif v is not None and not isinstance(v, (str, int, float, bool)):
                v = str(v)
            props[c] = v
        feats.append({"type": "Feature", "properties": props, "geometry": g})
    save_json(path, {"type": "FeatureCollection", "features": feats})
    return len(feats)


def _first(dfs):
    return dfs[0] if dfs else None


def run():
    ensure_dirs()
    report = {"started_at": utc_iso(), "layers": {}, "field_inference": {}, "blockers": []}
    layers, audits = {}, []
    gpkg = PROCESSED / "foundation.gpkg"
    if gpkg.exists():
        gpkg.unlink()

    for ds_id in ("admin_state", "admin_district", "glacial_lakes_cwc", "river_network_cwc"):
        try:
            vecs = find_vectors(ds_id)
            if not vecs:
                report["layers"][ds_id] = "MISSING (no raw or manual file)"
                report["blockers"].append(f"{ds_id}: no file found")
                log(f"foundation: {ds_id} missing")
                continue
            gdf, meta = read_vector(vecs[0])
            gdf, idinfo = standardise(gdf, ds_id)
            layers[ds_id] = gdf
            a = audit(gdf, ds_id, meta, idinfo)
            audits.append(a)
            report["layers"][ds_id] = f"OK ({len(gdf)} features)"
            log(f"foundation: {ds_id} {len(gdf)} features, invalid={a['invalid_geometries']}, dup_id_rows={a['duplicate_id_rows']}")
            try:
                out = gdf.copy()
                for c in out.columns:
                    if c != "geometry" and out[c].dtype == object:
                        out[c] = out[c].map(lambda v: json.dumps(v) if isinstance(v, (dict, list)) else v)
                out.to_file(gpkg, layer=ds_id, driver="GPKG", engine="pyogrio")
            except Exception as e:
                report["blockers"].append(f"{ds_id}: GeoPackage write failed ({short(e)}); web layers unaffected")
        except Exception as e:
            report["layers"][ds_id] = f"FAILED: {short(e)}"
            report["blockers"].append(f"{ds_id}: {short(e)}")
            log(f"foundation: {ds_id} FAILED {short(e)}")

    search = []
    # ---- states
    states = layers.get("admin_state")
    if states is not None:
        nf = infer_field([c for c in states.columns if c != "geometry"], NAME_PATTERNS["state"])
        report["field_inference"]["state_name_field"] = nf
        states["sid"] = ["S%d" % i for i in states["shd_src_row"]]
        states["name"] = states[nf].astype(str) if nf else states["shd_src_id"]
        s_web = states.copy()
        s_web["geometry"] = s_web.geometry.simplify(0.01, preserve_topology=True)
        write_geojson(WEB_DATA / "states.geojson", s_web, ["sid", "name", "shd_src_id"], nd=3)
        for _, r in states.iterrows():
            c = r.geometry.representative_point()
            search.append({"t": "state", "id": r["sid"], "n": r["name"], "lat": round(c.y, 3), "lon": round(c.x, 3)})
    else:
        report["blockers"].append("states layer missing: no map outline or state names")

    # ---- districts (state assigned by spatial join; labelled computed)
    dists = layers.get("admin_district")
    if dists is not None:
        nf = infer_field([c for c in dists.columns if c != "geometry"], NAME_PATTERNS["district"])
        report["field_inference"]["district_name_field"] = nf
        dists["did"] = ["D%d" % i for i in dists["shd_src_row"]]
        dists["name"] = dists[nf].astype(str) if nf else dists["shd_src_id"]
        dists["sid"], dists["state"] = None, None
        if states is not None:
            pts = dists.geometry.representative_point()
            qi, ti = states.sindex.query(pts.values, predicate="within")
            first = {}
            for a, b in zip(qi, ti):
                first.setdefault(int(a), int(b))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for i in range(len(dists)):
                    j = first.get(i)
                    if j is None:
                        j = int(states.geometry.distance(pts.iloc[i]).values.argmin())
                    dists.at[i, "sid"] = states.iloc[j]["sid"]
                    dists.at[i, "state"] = states.iloc[j]["name"]
        d_web = dists.copy()
        d_web["geometry"] = d_web.geometry.simplify(0.004, preserve_topology=True)
        write_geojson(WEB_DATA / "districts.geojson", d_web, ["did", "name", "sid", "state", "shd_src_id"], nd=3)
        for _, r in dists.iterrows():
            c = r.geometry.representative_point()
            search.append({"t": "district", "id": r["did"], "n": r["name"], "x": r["state"], "lat": round(c.y, 3), "lon": round(c.x, 3)})
        layers["admin_district"] = dists
    else:
        report["blockers"].append("districts layer missing: alerts cannot be matched to districts")

    # ---- glacial lakes (points for the web; basic props only - links are added in the connectivity stage)
    lakes = layers.get("glacial_lakes_cwc")
    if lakes is not None:
        nf = infer_field([c for c in lakes.columns if c != "geometry" and not c.startswith("shd_")], NAME_PATTERNS["lake"])
        af = infer_field([c for c in lakes.columns if c != "geometry" and not c.startswith("shd_")], NAME_PATTERNS["area"])
        report["field_inference"]["lake_name_field"], report["field_inference"]["lake_area_field"] = nf, af
        lakes["lid"] = ["L%d" % i for i in lakes["shd_src_row"]]
        lakes["name"] = lakes[nf].astype(str) if nf else None
        proj = lakes.to_crs(AREA_CRS)
        lakes["area_ha_calc"] = (proj.area / 1e4).values
        lakes["area_ha_src"] = lakes[af] if af and lakes[af].dtype != object else np.nan
        pts = lakes.geometry.representative_point()
        lakes["lon"], lakes["lat"] = pts.x.values, pts.y.values
        # state/district by location (computed)
        lakes["did"], lakes["state"] = None, None
        if dists is not None:
            qi, ti = dists.sindex.query(pts.values, predicate="within")
            for a, b in zip(qi, ti):
                if lakes.at[int(a), "did"] is None:
                    lakes.at[int(a), "did"] = dists.iloc[int(b)]["did"]
                    lakes.at[int(a), "state"] = dists.iloc[int(b)]["state"]
        lakes_pt = lakes.copy()
        lakes_pt["geometry"] = pts.values
        layers["glacial_lakes_cwc"] = lakes_pt
        write_geojson(WEB_DATA / "glacial_lakes.geojson", lakes_pt,
                      ["lid", "name", "area_ha_calc", "area_ha_src", "did", "state", "shd_src_id"], nd=4)
        for _, r in lakes_pt.iterrows():
            search.append({"t": "lake", "id": r["lid"], "n": r["name"] or ("Glacial lake " + r["lid"]), "x": r["state"],
                           "lat": round(r["lat"], 4), "lon": round(r["lon"], 4)})
    else:
        report["blockers"].append("glacial lakes layer missing")

    # persist for the next stages
    try:
        for k in ("admin_state", "admin_district", "glacial_lakes_cwc"):
            if k in layers:
                (INTERIM / "foundation").mkdir(parents=True, exist_ok=True)
                layers[k].to_pickle(INTERIM / "foundation" / f"{k}.pkl")
        if "river_network_cwc" in layers:
            layers["river_network_cwc"].to_pickle(INTERIM / "foundation" / "river_network_cwc.pkl")
    except Exception as e:
        report["blockers"].append(f"could not cache layers for later stages: {short(e)}")

    save_json(WEB_DATA / "search_index.json", search)
    save_json(WEB_DATA / "audit.json", {"generated_at": utc_iso(), "layers": audits})
    save_json(WEB_DATA / "field_inference.json", report["field_inference"], compact=False)
    report["finished_at"] = utc_iso()
    return report
