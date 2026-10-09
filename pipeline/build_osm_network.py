"""River graph from OpenStreetMap waterways - used when HydroRIVERS is not available.

HydroSHEDS forbids automated downloads (robots.txt of data.hydrosheds.org), so this builds the same kind of
reach graph from OpenStreetMap (Geofabrik India extract, ODbL):

  * OSM convention: a waterway way is drawn in the direction of flow (documented on the OSM wiki, Key:waterway).
    Downstream links are therefore COMPUTED from way direction + shared nodes. They are not hydrologically modelled
    and individual ways can be mapped backwards; every statement made from them is labelled "Computed".
  * Ways are cut at every node shared with another waterway (confluences), and each piece is one reach.
  * A reach continues into the reach that starts at its end node; at a fork the continuation of the same way wins,
    then river over stream, then the longer reach.
  * There is no discharge. Reaches are ranked by the number of mapped reaches upstream ("acc") and Strahler order, both computed here.
  * A reach with nothing downstream ends where the MAPPED network ends: that can be the sea, an inland sink,
    or simply a gap in the mapping. The site says so.

Output schema matches build_connectivity.load_hydrorivers (rid, next_down, len_km, dis, ord, main_riv, hybas12) plus acc and osm_name.
"""
import shutil
import subprocess
from array import array
from collections import deque
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from .common import INTERIM, MANUAL, RAW, log, short

RANK = {"river": 0, "stream": 1}
FILTER_BIG_MB = 300          # without the osmium command-line tool, files larger than this are refused (memory)


def find_pbf():
    for base in (MANUAL / "osm_india", RAW / "osm_india"):
        if base.exists():
            f = sorted(p for p in base.rglob("*.pbf") if p.is_file())
            if f:
                return f[0]
    return None


def _filtered(src):
    """Waterway-only extract (with its nodes) via the osmium tool; cached next to the interim data."""
    if not shutil.which("osmium"):
        if src.stat().st_size > FILTER_BIG_MB * 1e6:
            return None, "the osmium command-line tool is required for an extract this large (apt install osmium-tool)"
        return src, ""
    dst = INTERIM / "osm" / "waterways.osm.pbf"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        log("osm network: filtering waterways from " + src.name)
        subprocess.run(["osmium", "tags-filter", str(src), "w/waterway=river,stream", "-o", str(dst), "--overwrite"], check=True, capture_output=True)
    return dst, ""


def read_ways(path):
    import osmium

    class H(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.nodes, self.lon, self.lat = array("q"), array("d"), array("d")
            self.way_len, self.way_id, self.way_rank = array("q"), array("q"), array("b")
            self.names = {}

        def way(self, w):
            ww = w.tags.get("waterway")
            if ww not in RANK:
                return
            pts = [(nd.ref, nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
            if len(pts) < 2:
                return
            for ref, lo, la in pts:
                self.nodes.append(ref)
                self.lon.append(lo)
                self.lat.append(la)
            self.way_len.append(len(pts))
            self.way_id.append(w.id)
            self.way_rank.append(RANK[ww])
            nm = w.tags.get("name")
            if nm:
                self.names[len(self.way_id) - 1] = nm

    h = H()
    h.apply_file(str(path), locations=True, idx="flex_mem")
    return h


def build_reaches(nodes, lon, lat, way_len, way_rank, names):
    """Pure function (testable without a PBF). Inputs are flat arrays: one entry per node-occurrence, one per way."""
    nodes, lon, lat = (np.asarray(x) for x in (nodes, lon, lat))
    way_len, way_rank = np.asarray(way_len), np.asarray(way_rank)
    n_way, n_pts = len(way_len), len(nodes)
    way_start = np.cumsum(way_len) - way_len
    way_of = np.repeat(np.arange(n_way), way_len)
    u, c = np.unique(nodes, return_counts=True)
    shared = c[np.searchsorted(u, nodes)] >= 2
    brk = shared.copy()
    brk[way_start] = True
    brk[way_start + way_len - 1] = True
    B = np.flatnonzero(brk)
    s, e = B[:-1], B[1:]
    keep = way_of[s] == way_of[e]
    s, e = s[keep], e[keep]
    nseg = len(s)
    if nseg == 0:
        return None, {"segments": 0}
    npts = e - s + 1
    total = int(npts.sum())
    seg_of_pt = np.repeat(np.arange(nseg), npts)
    gidx = np.repeat(s - (np.cumsum(npts) - npts), npts) + np.arange(total)
    xs, ys = lon[gidx], lat[gidx]
    geoms = shapely.linestrings(np.column_stack((xs, ys)), indices=seg_of_pt)
    same = seg_of_pt[1:] == seg_of_pt[:-1]
    dx = np.diff(xs) * 111.32 * np.cos(np.radians(ys[1:]))
    dy = np.diff(ys) * 110.57
    seg_len = np.bincount(seg_of_pt[1:][same], weights=np.hypot(dx, dy)[same], minlength=nseg)
    seg_way = way_of[s]
    df = pd.DataFrame({"sid": np.arange(nseg), "way": seg_way, "start": nodes[s], "end": nodes[e], "rank": way_rank[seg_way], "len": seg_len})
    m = df[["sid", "way", "end"]].merge(df[["sid", "way", "start", "rank", "len"]], left_on="end", right_on="start", suffixes=("", "_r"))
    m = m[m["sid"] != m["sid_r"]].copy()
    m["same"] = m["way"] == m["way_r"]
    m = m.sort_values(["sid", "same", "rank", "len", "sid_r"], ascending=[True, False, True, False, True])
    ncand = m.groupby("sid").size()
    first = m.drop_duplicates("sid")
    nxt = np.full(nseg, -1, dtype="int64")
    nxt[first["sid"].values] = first["sid_r"].values

    # computed hydrology-free ranking: upstream reach count (acc) and Strahler order, processed upstream -> downstream
    indeg = np.bincount(nxt[nxt >= 0], minlength=nseg).tolist()
    nl = nxt.tolist()
    order, acc, maxin, cntmax = [1] * nseg, [1] * nseg, [0] * nseg, [0] * nseg
    q = deque(i for i in range(nseg) if indeg[i] == 0)
    done = 0
    while q:
        i = q.popleft()
        done += 1
        if maxin[i] > 0:
            order[i] = maxin[i] + 1 if cntmax[i] >= 2 else maxin[i]
        j = nl[i]
        if j >= 0:
            acc[j] += acc[i]
            if order[i] > maxin[j]:
                maxin[j], cntmax[j] = order[i], 1
            elif order[i] == maxin[j]:
                cntmax[j] += 1
            indeg[j] -= 1
            if indeg[j] == 0:
                q.append(j)
    way_name = [names.get(int(w)) for w in seg_way]
    gdf = gpd.GeoDataFrame({
        "rid": np.arange(1, nseg + 1, dtype="int64"),
        "next_down": np.where(nxt >= 0, nxt + 1, 0).astype("int64"),
        "len_km": seg_len, "dis": np.nan, "ord": np.array(order, dtype="float64"), "main_riv": np.nan, "hybas12": np.nan,
        "acc": np.array(acc, dtype="float64"), "osm_name": way_name}, geometry=geoms, crs="EPSG:4326")
    stats = {"ways": int(n_way), "reaches": int(nseg), "junction_nodes": int((c >= 2).sum()), "forks_resolved_by_rule": int((ncand > 1).sum()),
             "reaches_without_downstream": int((nxt < 0).sum()), "reaches_in_cycles": int(nseg - done), "total_km": round(float(seg_len.sum()), 1)}
    return gdf, stats


def load_osm_waterways():
    src = find_pbf()
    if src is None:
        return None, None, "OpenStreetMap PBF not found (data/raw/osm_india or data/manual/osm_india); run the static stage with --heavy"
    try:
        import osmium  # noqa: F401
    except ImportError:
        return None, None, "pyosmium not installed (pip install osmium)"
    path, why = _filtered(src)
    if path is None:
        return None, None, why
    h = read_ways(path)
    if not len(h.way_id):
        return None, None, "OpenStreetMap file read but it holds no waterway=river/stream ways"
    gdf, stats = build_reaches(h.nodes, h.lon, h.lat, h.way_len, h.way_rank, h.names)
    if gdf is None:
        return None, None, "no reaches could be built from the OpenStreetMap waterways"
    stats["source_file"] = src.name
    return gdf, stats, f"{stats['reaches']:,} reaches from {stats['ways']:,} OpenStreetMap waterway ways ({src.name})"
