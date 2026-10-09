"""TEST ONLY. OSM-derived river graph on a fictional mini network (temporary root).   python tests/test_osm_network.py"""
import json, os, shutil, sys, tempfile, zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="shd_osm_"))
shutil.copytree(PROJECT / "config", TMP / "config")
(TMP / "web").mkdir()
os.environ["SHAILDHARA_ROOT"] = str(TMP)
os.environ["SHAILDHARA_DELAY"] = "0"
sys.path.insert(0, str(PROJECT))

import geopandas as gpd
import osmium
from shapely.geometry import box
from pipeline import build_connectivity, build_foundation, build_osm_network
from pipeline.common import MANUAL, WEB_DATA, ensure_dirs

ensure_dirs()
ok = 0
def check(c, m):
    global ok
    assert c, "FAIL: " + m
    ok += 1
    print("ok  :", m)

# --- fictional waterways (lon, lat). Main river M flows south: n1..n7. Tributary T joins at n3. Distributary D leaves at n5. Stream S is isolated.
N = {1: (78.2, 31.9), 2: (78.2, 31.6), 3: (78.2, 31.3), 4: (78.2, 31.0), 5: (78.2, 30.7), 6: (78.2, 30.4), 7: (78.2, 30.1),
     11: (78.6, 31.6), 12: (78.4, 31.45), 21: (78.5, 30.5), 22: (78.5, 30.2), 31: (79.5, 29.5), 32: (79.5, 29.2)}
W = [(101, [1, 2, 3, 4, 5, 6, 7], {"waterway": "river", "name": "Testganga"}), (102, [11, 12, 3], {"waterway": "stream"}),
     (103, [5, 21, 22], {"waterway": "stream"}), (104, [31, 32], {"waterway": "stream", "name": "Lonely"}), (105, [1, 2], {"waterway": "canal"})]
pbf = MANUAL / "osm_india" / "mini.osm.pbf"
pbf.parent.mkdir(parents=True, exist_ok=True)
wr = osmium.SimpleWriter(str(pbf))
for nid, (lo, la) in N.items():
    wr.add_node(osmium.osm.mutable.Node(id=nid, location=(lo, la)))
for wid, refs, tags in W:
    wr.add_way(osmium.osm.mutable.Way(id=wid, nodes=refs, tags=tags))
wr.close()

g, stats = build_osm_network.build_reaches(*(lambda h: (h.nodes, h.lon, h.lat, h.way_len, h.way_rank, h.names))(build_osm_network.read_ways(pbf)))
check(stats["ways"] == 4, "canal ways are ignored; river and stream ways are read")
by = {tuple(map(round, (r.geometry.coords[0][1] * 10, r.geometry.coords[-1][1] * 10))): r for r in g.itertuples()}
M1 = by[(319, 313)]; M2 = by[(313, 307)]; M3 = by[(307, 301)]
check(len(g) == 6, f"ways are cut at confluences and forks: {len(g)} reaches (main river x3, tributary, distributary, isolated stream)")
check(M1.next_down == M2.rid and M2.next_down == M3.rid, "the main river continues into itself, not into the tributary or fork")
T = g[g.geometry.apply(lambda l: abs(l.coords[0][0] - 78.6) < 1e-6)].iloc[0]
check(T.next_down == M2.rid, "the tributary flows into the reach that starts at the confluence")
check(M2.acc == 3 and M2.ord == 2, f"two first-order streams make a second-order reach (ord={M2.ord}, acc={M2.acc})")
lonely = g[g.osm_name == "Lonely"].iloc[0]
check(lonely.next_down == 0, "an isolated stream has no downstream reach (end of the mapped network)")
check(stats["reaches_in_cycles"] == 0, "no cycles in the fixture")

# --- full connectivity stage with this PBF and NO HydroRIVERS
states = gpd.GeoDataFrame({"ST_NM": ["Teststate"], "ST_CEN_CD": ["01"]}, geometry=[box(77, 28, 80, 32)], crs=4326)
dists = gpd.GeoDataFrame({"DT_NAME": ["Alpha", "Beta"], "DT_CEN_CD": ["1", "2"]}, geometry=[box(77, 28, 78.5, 32), box(78.5, 28, 80, 32)], crs=4326)
lakes = gpd.GeoDataFrame({"Lake_Name": ["Closelake"], "Area_ha": [3.2]}, geometry=[box(78.201, 31.60, 78.2015, 31.6005)], crs=4326)
for name, gdf, ds in (("state", states, "admin_state"), ("district", dists, "admin_district")):
    d = MANUAL / ds; d.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / f"{name}.geojson"; gdf.to_file(tmp, driver="GeoJSON")
    with zipfile.ZipFile(d / f"{name}_nwic_geojson.zip", "w") as z: z.write(tmp, f"{name}.geojson")
for ds, gdf, fn in (("glacial_lakes_cwc", lakes, "glacial_lake.geojson"),):
    d = MANUAL / ds; d.mkdir(parents=True, exist_ok=True); gdf.to_file(d / fn, driver="GeoJSON")
fr = build_foundation.run()
check(all(v.startswith("OK") for k, v in fr["layers"].items() if k != "river_network_cwc") , f"foundation OK: {fr['layers']}")
cn = build_connectivity.run()
check(cn["status"] == "OK" and cn["stats"]["river_source"] == "osm_waterways", f"connectivity runs on the OSM graph: {cn['status']} {cn['stats'].get('river_source')}")
net = json.load(open(WEB_DATA / "network" / "reaches.json"))
check(net["method"] == "osm_waterways" and "OpenStreetMap" in net["source"] and net["dis_cms"].count(None) == len(net["ids"]), "network is labelled OSM-derived and carries no invented discharge")
check("Testganga" in net["names"], "OSM river names are used when CWC names are absent")
lk = json.load(open(WEB_DATA / "glacial_lakes.geojson"))
check(lk["features"][0]["properties"]["quality"] == "close", "glacial lake snaps to the OSM reach")
print(f"ALL OSM NETWORK TESTS PASSED ({ok})")
