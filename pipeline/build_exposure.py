"""Stage 4 - exposure along river reaches (spatial association only, NOT damage or impact).

For every exported reach a corridor (CORRIDOR_M each side) is built and datasets are summarised inside it.
A dataset that is not loaded produces NO values (listed under 'unavailable'): missing data is never turned into zero.
Cells/features are assigned to one reach only, so consecutive reaches do not double count.
"""
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, box

from .build_foundation import NAME_PATTERNS, STORAGE_CRS, find_vectors, infer_field, read_vector
from .common import INTERIM, MANUAL, RAW, WEB_DATA, ensure_dirs, log, read_json, save_json, short, utc_iso

PROJ_CRS = "EPSG:7755"
CORRIDOR_M = 2000


def _files(ds_id, exts):
    out = []
    for base in (MANUAL / ds_id, RAW / ds_id):
        if base.exists():
            out += [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix.lower() in exts and "_extracted" not in p.parts]
        if out:
            break
    return out


def load_reach_geometries():
    net = read_json(WEB_DATA / "network" / "reaches.json")
    if not net:
        return None, None
    geoms = [LineString(c) if len(c) >= 2 else None for c in net["coords"]]
    gdf = gpd.GeoDataFrame({"ri": np.arange(len(geoms))}, geometry=geoms, crs=STORAGE_CRS)
    gdf = gdf[gdf.geometry.notna()].reset_index(drop=True)
    return net, gdf


def population(buf4326, n, meta):
    tifs = _files("worldpop_india", (".tif", ".tiff"))
    if not tifs:
        return None, "WorldPop raster not loaded (candidate download not run/blocked; place a GeoTIFF in data/manual/worldpop_india/)"
    import rasterio
    from rasterio import features
    with rasterio.open(tifs[0]) as src:
        shapes_gdf = buf4326 if (src.crs is None or src.crs.to_epsg() == 4326) else buf4326.to_crs(src.crs)
        arr = src.read(1, masked=True)
        label = features.rasterize(((g, int(k) + 1) for g, k in zip(shapes_gdf.geometry, shapes_gdf["ri"])),
                                   out_shape=src.shape, transform=src.transform, fill=0, all_touched=True, dtype="int32")
        data = arr.filled(0).astype("float64")
        data[~np.isfinite(data)] = 0
        valid = (label > 0) & (~np.ma.getmaskarray(arr)) & (data > 0)
        sums = np.bincount(label[valid], weights=data[valid], minlength=n + 1)[1:]
        left, bottom, right, top = src.bounds
    covered = np.array([box(left, bottom, right, top).intersects(g) for g in buf4326.geometry])
    vals = [None] * n
    for k, ri in enumerate(buf4326["ri"].values):
        if covered[k]:
            vals[int(ri)] = int(round(sums[int(ri)]))
    meta["population"] = dict(label="Population within corridor", unit="people (modelled)", dataset=f"WorldPop 1 km ({tifs[0].name})",
                              method=f"Each raster cell assigned to at most one reach; cells within {CORRIDOR_M} m of the reach summed",
                              caveats="Modelled estimate, not a census; cells near reach joins go to one reach only; indicative, not exact")
    return vals, "ok"


def landcover(buf4326, n, meta):
    tifs = _files("landcover", (".tif", ".tiff"))
    if not tifs:
        return None, "Land-cover tiles not loaded (place ESA WorldCover or NRSC GeoTIFF tiles in data/manual/landcover/)"
    import rasterio
    from rasterio import features
    from rasterio.windows import Window
    crop = np.zeros(n + 1)
    built = np.zeros(n + 1)
    covered = np.zeros(n, dtype=bool)
    sidx = buf4326.sindex
    geoms = buf4326.geometry.values
    ris = buf4326["ri"].values
    for tif in tifs:
        with rasterio.open(tif) as src:
            tb = box(*src.bounds)
            for k in sidx.query(tb, predicate="intersects"):
                covered[int(ris[int(k)])] = True
            step = 2048
            for row in range(0, src.height, step):
                for col in range(0, src.width, step):
                    win = Window(col, row, min(step, src.width - col), min(step, src.height - row))
                    cand = sidx.query(box(*src.window_bounds(win)), predicate="intersects")
                    if len(cand) == 0:
                        continue
                    arr = src.read(1, window=win)
                    tr = src.window_transform(win)
                    label = features.rasterize(((geoms[int(c)], int(ris[int(c)]) + 1) for c in cand), out_shape=arr.shape,
                                               transform=tr, fill=0, dtype="int32")
                    lat = src.window_bounds(win)[1] / 2 + src.window_bounds(win)[3] / 2
                    px_ha = abs(tr.a) * 111320 * math.cos(math.radians(lat)) * abs(tr.e) * 110574 / 1e4
                    for code, store in ((40, crop), (50, built)):
                        m = (label > 0) & (arr == code)
                        if m.any():
                            store += np.bincount(label[m], minlength=n + 1)[: n + 1] * px_ha
    c_vals = [round(float(crop[i + 1]), 1) if covered[i] else None for i in range(n)]
    b_vals = [round(float(built[i + 1]), 1) if covered[i] else None for i in range(n)]
    meta["cropland_ha"] = dict(label="Cropland within corridor", unit="hectares", dataset="land cover tiles: " + ", ".join(t.name for t in tifs[:4]),
                               method="Pixels of class 40 (WorldCover cropland) counted inside the corridor",
                               caveats="Class codes assume ESA WorldCover; verify if you supplied another product. Reaches outside the supplied tiles have no value.")
    meta["builtup_ha"] = dict(label="Built-up area within corridor", unit="hectares", dataset="land cover tiles: " + ", ".join(t.name for t in tifs[:4]),
                              method="Pixels of class 50 (WorldCover built-up) counted inside the corridor",
                              caveats="Same tile coverage limits as cropland")
    return {"cropland_ha": c_vals, "builtup_ha": b_vals}, "ok"


def osm_assets(lines_p, n, meta):
    pbfs = _files("osm_india", (".pbf",))
    if not pbfs:
        return None, "OpenStreetMap PBF not loaded (heavy download not run/blocked; place a PBF in data/manual/osm_india/)"
    try:
        import osmium
    except ImportError:
        return None, "pyosmium not installed (pip install osmium) - OpenStreetMap step skipped"
    src = pbfs[0]
    if shutil.which("osmium"):
        filt = INTERIM / "osm" / "filtered.osm.pbf"
        filt.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["osmium", "tags-filter", str(src), "n/place=city,town,village,hamlet", "w/highway=motorway,trunk,primary,secondary,tertiary",
                            "w/bridge", "w/man_made=bridge", "w/waterway=dam", "w/man_made=dam", "w/power=plant", "-o", str(filt), "--overwrite"],
                           check=True, capture_output=True)
            src = filt
        except Exception as e:
            log("osm: tag filter failed, reading the full file (slow): " + short(e))
    rows = []

    class Handler(osmium.SimpleHandler):
        def node(self, nd):
            p = nd.tags.get("place")
            if p in ("city", "town", "village", "hamlet") and nd.location.valid():
                rows.append(("place_" + ("city_town" if p in ("city", "town") else "village_hamlet"), nd.location.lon, nd.location.lat))

        def way(self, w):
            t = w.tags
            kind = None
            if t.get("bridge") not in (None, "no") or t.get("man_made") == "bridge":
                kind = "bridges"
            elif t.get("waterway") == "dam" or t.get("man_made") == "dam":
                kind = "dams"
            elif t.get("power") == "plant" and t.get("plant:source") == "hydro":
                kind = "hydropower_plants"
            elif t.get("highway") in ("motorway", "trunk", "primary"):
                kind = "roads_major"
            elif t.get("highway") in ("secondary", "tertiary"):
                kind = "roads_secondary_tertiary"
            if kind is None:
                return
            xs, ys = [], []
            for nd in w.nodes:
                if nd.location.valid():
                    xs.append(nd.location.lon)
                    ys.append(nd.location.lat)
            if xs:
                rows.append((kind, sum(xs) / len(xs), sum(ys) / len(ys)))

    Handler().apply_file(str(src), locations=True, idx="flex_mem")
    if not rows:
        return None, "OpenStreetMap file read but no matching features found"
    pts = gpd.GeoDataFrame({"kind": [r[0] for r in rows]}, geometry=gpd.points_from_xy([r[1] for r in rows], [r[2] for r in rows]), crs=STORAGE_CRS).to_crs(PROJ_CRS)
    j = gpd.sjoin_nearest(pts, lines_p[["ri", "geometry"]], how="inner", max_distance=CORRIDOR_M, distance_col="d")
    j = j.sort_values("d")
    j = j[~j.index.duplicated(keep="first")]   # each feature goes to its nearest reach only
    counts = defaultdict(lambda: np.zeros(n, dtype=int))
    for kind, ri in zip(j["kind"], j["ri"]):
        counts[kind][int(ri)] += 1
    labels = {"place_city_town": "Cities/towns (OSM place nodes)", "place_village_hamlet": "Villages/hamlets (OSM place nodes)",
              "bridges": "Bridges (OSM)", "dams": "Dams (OSM)", "hydropower_plants": "Hydropower plants (OSM)",
              "roads_major": "Major road segments (OSM motorway/trunk/primary)", "roads_secondary_tertiary": "Secondary/tertiary road segments (OSM)"}
    out = {}
    for k, lab in labels.items():
        out["osm_" + k] = [int(v) for v in counts[k]] if k in counts else [0] * n
        meta["osm_" + k] = dict(label=lab, unit="count within corridor", dataset=f"OpenStreetMap ({Path(pbfs[0]).name})",
                                method=f"Feature (centroid) assigned to the nearest reach within {CORRIDOR_M} m",
                                caveats="Crowd-sourced; mountain areas are often under-mapped so a zero can mean 'not mapped'. Segments, not kilometres.")
    return out, "ok"


def ecosystems(buf4326, n, meta, names):
    files = _files("ecosystems", (".geojson", ".json", ".shp", ".gpkg", ".zip"))
    results = {}
    if not files:
        return None, "No ecosystem polygon files provided (data/manual/ecosystems/)"
    for f in files:
        try:
            if f.suffix.lower() == ".zip":
                vecs = find_vectors("ecosystems")
                path = vecs[0] if vecs else None
            else:
                path = f
            if path is None:
                continue
            gdf, _m = read_vector(path)
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
            nf = infer_field([c for c in gdf.columns if c != "geometry"], NAME_PATTERNS["river"])
            j = gpd.sjoin(buf4326[["ri", "geometry"]], gdf[[nf, "geometry"]] if nf else gdf[["geometry"]], how="inner", predicate="intersects")
            key = "eco_" + "".join(ch if ch.isalnum() else "_" for ch in f.stem.lower())[:30]
            arr = np.zeros(n, dtype=int)
            nm = defaultdict(list)
            for ri, g in j.groupby("ri"):
                arr[int(ri)] = len(g)
                if nf:
                    for v in g[nf].dropna().astype(str).unique()[:3]:
                        nm[int(ri)].append(v)
            results[key] = [int(v) for v in arr]
            names[key] = {str(k): v for k, v in nm.items()}
            meta[key] = dict(label=f"Polygons from '{f.name}' touching the corridor", unit="count", dataset=f.name,
                             method=f"Polygon intersects the {CORRIDOR_M} m corridor",
                             caveats="Only the files you supplied are covered; a zero means none in those files, not that none exist.")
        except Exception as e:
            log(f"ecosystems: {f.name} failed - {short(e)}")
    return (results or None), ("ok" if results else "ecosystem files could not be read")


def run():
    ensure_dirs()
    rep = {"started_at": utc_iso(), "status": "NOT_RUN", "blockers": []}
    net, reaches = load_reach_geometries()
    if net is None:
        rep.update(status="BLOCKED")
        rep["blockers"].append("No river network exported (connectivity stage blocked), so exposure along rivers cannot be computed")
        return rep
    n = len(net["ids"])
    reaches_p = reaches.to_crs(PROJ_CRS)
    buf = gpd.GeoDataFrame({"ri": reaches["ri"].values}, geometry=reaches_p.geometry.buffer(CORRIDOR_M).to_crs(STORAGE_CRS).values, crs=STORAGE_CRS)
    layers, meta, names, unavailable = {}, {}, {}, []

    def attempt(label, fn, *args):
        try:
            vals, why = fn(*args)
        except Exception as e:
            vals, why = None, f"failed: {short(e)}"
        if vals is None:
            unavailable.append({"key": label, "reason": why})
            log(f"exposure: {label} unavailable - {why}")
        else:
            layers.update(vals if isinstance(vals, dict) else {label: vals})
            log(f"exposure: {label} OK")

    attempt("population", population, buf, n, meta)
    attempt("land_cover", landcover, buf, n, meta)
    attempt("osm_assets", osm_assets, reaches_p, n, meta)
    attempt("ecosystems", ecosystems, buf, n, meta, names)
    out = {"generated_at": utc_iso(), "corridor_m": CORRIDOR_M, "n": n,
           "note": "Spatial association only: assets located near a traced river. Not damage, not impact, not a forecast.",
           "layers": {k: {**meta.get(k, {}), "values": v} for k, v in layers.items()}, "unavailable": unavailable, "names": names}
    save_json(WEB_DATA / "exposure" / "reach_exposure.json", out)
    rep["status"] = "OK" if layers else "NO_LAYERS"
    rep["layers"] = sorted(layers)
    rep["unavailable"] = unavailable
    rep["finished_at"] = utc_iso()
    return rep
