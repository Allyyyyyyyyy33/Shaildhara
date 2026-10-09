"""TEST ONLY. Builds throwaway synthetic fixtures inside a TEMPORARY root to exercise the pipeline code.
This data is fictional and is never written into the real project tree or shown on the website.

    python tests/test_pipeline.py
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="shd_test_"))
shutil.copytree(PROJECT / "config", TMP / "config")
(TMP / "web").mkdir()
os.environ["SHAILDHARA_ROOT"] = str(TMP)
os.environ["SHAILDHARA_DELAY"] = "0"
sys.path.insert(0, str(PROJECT))

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from pipeline import build_all, build_connectivity, build_exposure, build_foundation, fetch_live, fetch_static
from pipeline.common import MANUAL, WEB_DATA, WEB_LIVE, Http

M = MANUAL
for d in ("admin_state", "admin_district", "glacial_lakes_cwc", "river_network_cwc", "hydrorivers_asia", "worldpop_india", "landcover", "ecosystems"):
    (M / d).mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------- fictional fixtures
states = gpd.GeoDataFrame({"ST_NM": ["Teststate A", "Teststate B"], "ST_CEN_CD": ["01", "02"]},
                          geometry=[box(77, 28, 80, 30), box(77, 30, 80, 32)], crs=4326)
dists = gpd.GeoDataFrame({"DT_NAME": ["Alpha", "Beta", "Gamma", "Delta"], "DT_CEN_CD": ["1", "2", "3", "4"]},
                         geometry=[box(77, 28, 78.5, 30), box(78.5, 28, 80, 30), box(77, 30, 78.5, 32), box(78.5, 30, 80, 32)], crs=4326)


def zipit(gdf, folder, name, inner):
    tmp = Path(tempfile.mkdtemp()) / inner
    gdf.to_file(tmp, driver="GeoJSON")
    with zipfile.ZipFile(M / folder / name, "w") as z:
        z.write(tmp, inner)


zipit(states, "admin_state", "state_nwic_geojson.zip", "state.geojson")
zipit(dists, "admin_district", "district_nwic_geojson.zip", "district.geojson")
rivers_cwc = gpd.GeoDataFrame({"River_Name": ["Testganga"]}, geometry=[LineString([(78.2, 31.9), (78.2, 28.2)])], crs=4326)
rivers_cwc.to_file(M / "river_network_cwc" / "river_network.geojson", driver="GeoJSON")
lakes = gpd.GeoDataFrame({"Lake_Name": ["Closelake", "Midlake", "Farlake"], "Area_ha": [3.2, 5.1, 2.0]},
                         geometry=[box(78.201, 31.80, 78.2015, 31.8005), box(78.25, 31.5, 78.2505, 31.5005), box(79.9, 31.9, 79.9005, 31.9005)], crs=4326)
lakes.to_file(M / "glacial_lakes_cwc" / "glacial_lake.geojson", driver="GeoJSON")

# synthetic HydroRIVERS: main stem 101..109 flowing south, tributary 201 joining 103, isolated terminal river 301, 110 lies outside India
lats = np.linspace(31.9, 27.0, 11)
rows, geoms = [], []
for k in range(10):
    rid = 101 + k
    nd = rid + 1 if k < 9 else 0
    rows.append(dict(HYRIV_ID=rid, NEXT_DOWN=nd, LENGTH_KM=50.0, DIS_AV_CMS=10.0 + k * 5, ORD_STRA=2 + k // 3, MAIN_RIV=101, HYBAS_L12=9000 + k))
    geoms.append(LineString([(78.2, lats[k]), (78.2, lats[k + 1])]))
rows.append(dict(HYRIV_ID=201, NEXT_DOWN=104, LENGTH_KM=30.0, DIS_AV_CMS=4.0, ORD_STRA=1, MAIN_RIV=101, HYBAS_L12=9100))
geoms.append(LineString([(79.0, 31.0), (78.2, 30.95)]))
rows.append(dict(HYRIV_ID=301, NEXT_DOWN=0, LENGTH_KM=20.0, DIS_AV_CMS=1.0, ORD_STRA=1, MAIN_RIV=301, HYBAS_L12=9200))
geoms.append(LineString([(79.5, 29.5), (79.5, 29.0)]))
hr = gpd.GeoDataFrame(rows, geometry=geoms, crs=4326)
td = Path(tempfile.mkdtemp())
hr.to_file(td / "HydroRIVERS_v10_as.shp")
with zipfile.ZipFile(M / "hydrorivers_asia" / "HydroRIVERS_v10_as_shp.zip", "w") as z:
    for f in td.iterdir():
        z.write(f, f.name)

# population raster 0.05 deg
W, H = 80, 100
tr = from_origin(77.0, 32.0, 0.05, 0.05)
pop = np.full((H, W), 10.0, dtype="float32")
with rasterio.open(M / "worldpop_india" / "test_pop.tif", "w", driver="GTiff", height=H, width=W, count=1, dtype="float32", crs="EPSG:4326", transform=tr, nodata=-1) as dst:
    dst.write(pop, 1)
# land cover raster covering only the northern half (to test 'no value outside tiles')
lc = np.zeros((50, 60), dtype="uint8"); lc[:, :] = 40
tr2 = from_origin(77.5, 32.0, 0.01 * 5, 0.01 * 5)
with rasterio.open(M / "landcover" / "test_cover.tif", "w", driver="GTiff", height=50, width=60, count=1, dtype="uint8", crs="EPSG:4326", transform=tr2) as dst:
    dst.write(lc, 1)
eco = gpd.GeoDataFrame({"NAME": ["Test Sanctuary"]}, geometry=[box(78.0, 29.0, 78.4, 29.5)], crs=4326)
eco.to_file(M / "ecosystems" / "protected.geojson", driver="GeoJSON")

# ----------------------------------------------------------------- run the stages
fs = fetch_static.run()
assert fs["datasets"]["admin_state"]["status"] == "OK_MANUAL", fs["datasets"]["admin_state"]
assert fs["datasets"]["osm_india"]["status"] in ("SKIPPED_HEAVY", "BLOCKED_MANUAL"), fs["datasets"]["osm_india"]
fr = build_foundation.run()
print("foundation:", fr["layers"], fr["field_inference"])
assert all(v.startswith("OK") for v in fr["layers"].values()), fr["layers"]
cn = build_connectivity.run()
print("connectivity:", cn["status"], cn["stats"], cn["blockers"])
assert cn["status"] == "OK"
assert cn["stats"]["reaches_leaving_selection"] == 1, cn["stats"]       # reach 109 points to 110 which lies outside India
assert cn["stats"]["lake_links"]["unlinked"] == 1 and cn["stats"]["lake_links"]["close_le_1km"] == 1, cn["stats"]["lake_links"]
ex = build_exposure.run()
print("exposure:", ex["status"], ex.get("layers"), [u["key"] for u in ex.get("unavailable", [])])
assert "population" in ex["layers"] and any(u["key"] == "osm_assets" for u in ex["unavailable"])
net = json.load(open(WEB_DATA / "network" / "reaches.json"))
expo = json.load(open(WEB_DATA / "exposure" / "reach_exposure.json"))
assert len(expo["layers"]["population"]["values"]) == len(net["ids"])
assert any(v is None for v in expo["layers"]["cropland_ha"]["values"]), "reaches outside the land-cover tile must have NO value, not zero"
assert all(v is not None for v in expo["layers"]["population"]["values"])
lk = json.load(open(WEB_DATA / "glacial_lakes.geojson"))
q = {f["properties"]["name"]: f["properties"]["quality"] for f in lk["features"]}
print("lake link quality:", q)
assert q["Closelake"] == "close" and q["Farlake"] == "unlinked"

# ----------------------------------------------------------------- live parsers with fixture XML / fake HTTP
CAP = """<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>X1</identifier><sender>imd@test</sender>
<sent>{sent}</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
<info><category>Met</category><event>Heavy Rainfall</event><urgency>Expected</urgency><severity>Severe</severity><certainty>Likely</certainty>
<expires>{exp}</expires><senderName>India Meteorological Department</senderName><headline>Heavy rain likely</headline><description>test</description>
<area><areaDesc>Alpha, Gamma</areaDesc></area></info></alert>"""
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc)
cap_ok = CAP.format(sent=now.isoformat(), exp=(now + timedelta(hours=12)).isoformat()).encode()
cap_old = CAP.replace("X1", "X2").format(sent=(now - timedelta(days=3)).isoformat(), exp=(now - timedelta(days=2)).isoformat()).encode()
RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>a</title><link>https://sachet.test/cap/X1.xml</link></item>
<item><title>b</title><link>https://sachet.test/cap/X2.xml</link></item></channel></rss>""".encode()


class FakeHttp:
    def __init__(self, routes): self.routes = routes
    def get(self, url, params=None, headers=None, etag=None, max_bytes=0, timeout=None):
        for k, v in self.routes.items():
            if url.startswith(k):
                if callable(v): v = v(url, params, headers)
                return {"ok": True, "skipped": False, "unreachable": False, "status": v[0], "headers": v[2] if len(v) > 2 else {}, "content": v[1], "error": "", "truncated": False}
        return {"ok": False, "skipped": False, "unreachable": True, "status": None, "headers": {}, "content": b"", "error": "no route (test)"}


import pandas as pd
dist_pkl = pd.read_pickle(TMP / "data" / "interim" / "foundation" / "admin_district.pkl")
cfg = json.load(open(PROJECT / "config" / "live_sources.json"))
cfg["sachet"].update(base="https://sachet.test", feeds=["https://sachet.test/rss/test.xml"], discover_from_capfeed_page=False, try_state_pattern_guesses=False)
status = {"sources": {}}
fake = FakeHttp({"https://sachet.test/rss/test.xml": (200, RSS, {"ETag": "e1"}), "https://sachet.test/cap/X1.xml": (200, cap_ok),
                 "https://sachet.test/cap/X2.xml": (200, cap_old)})
fetch_live.sachet(fake, cfg, dist_pkl, status)
al = json.load(open(WEB_LIVE / "alerts.json"))
print("sachet:", status["sources"]["sachet"]["status"], [(a["id"], a["match_method"], a["districts"]) for a in al["alerts"]])
assert len(al["alerts"]) == 1 and al["alerts"][0]["id"] == "X1", "expired alert must be dropped"
assert al["alerts"][0]["match_method"] == "NAME_MATCH" and len(al["alerts"][0]["districts"]) == 2

# polygon matching (CAP is lat,lon)
a2 = {"polygons": ["28.5,77.5 28.5,78.0 29.0,78.0 29.0,77.5 28.5,77.5"], "areas": []}
ids, how = fetch_live.match_districts(a2, dist_pkl)
assert how == "POLYGON" and len(ids) == 1, (ids, how)

# SACHET unreachable => BLOCKED with a reason, no invented alerts
status = {"sources": {}}
fetch_live.sachet(FakeHttp({}), cfg, dist_pkl, status)
assert status["sources"]["sachet"]["status"] == "BLOCKED"
assert json.load(open(WEB_LIVE / "alerts.json"))["alerts"] == []

# IMD: no key => NEEDS_API_KEY, nothing fabricated
os.environ.pop("IMD_API_KEY", None)
status = {"sources": {}}
fetch_live.imd(FakeHttp({}), cfg, dist_pkl, status)
assert status["sources"]["imd"]["status"] == "NEEDS_API_KEY"
# IMD with a (fake) key
os.environ["IMD_API_KEY"] = "dummy-test-key"
seen = {}
def imd_route(url, params, headers):
    seen["h"] = headers
    return (200, json.dumps([{"District": "Alpha", "Day1": 3}]).encode())
status = {"sources": {}}
fetch_live.imd(FakeHttp({"https://api.imd.gov.in/api/v1/": imd_route}), cfg, dist_pkl, status)
print("imd:", status["sources"]["imd"]["status"], status["sources"]["imd"]["detail"])
assert status["sources"]["imd"]["status"] == "OK" and seen["h"]["Authorization"] == "Bearer dummy-test-key"
raw = json.load(open(WEB_LIVE / "imd_district_warnings.json"))
assert raw["rows"][0]["district_ids"] and raw["rows"][0]["raw"]["Day1"] == 3
os.environ.pop("IMD_API_KEY", None)

# NWDP / CKAN
today = now.isoformat()
ckan_search = json.dumps({"success": True, "result": {"results": [{"id": "d1", "name": "tele-test", "title": "Telemetry test", "metadata_modified": today,
        "organization": {"title": "CWC"}, "resources": [{"id": "r1", "format": "CSV", "datastore_active": True}]}]}}).encode()
ckan_rows = json.dumps({"success": True, "result": {"fields": [{"id": "_id"}, {"id": "Station"}, {"id": "Latitude"}, {"id": "Longitude"}, {"id": "Data Acquisition Time"}, {"id": "River Water Level (meter)"}],
        "records": [{"Station": "S1", "Latitude": 29.1, "Longitude": 78.1, "Data Acquisition Time": today, "River Water Level (meter)": 101.5}]}}).encode()
fakec = FakeHttp({"https://nwdp.test/api/3/action/package_search": (200, ckan_search), "https://nwdp.test/api/3/action/datastore_search": (200, ckan_rows)})
cfg["nwdp"]["host"] = "https://nwdp.test"; cfg["nwdp"]["queries"] = ["x"]
status = {"sources": {}}
fetch_live.nwdp(fakec, cfg, status)
print("nwdp:", status["sources"]["nwdp_cwc"]["status"], status["sources"]["nwdp_cwc"]["detail"])
assert status["sources"]["nwdp_cwc"]["status"] == "OK" and status["sources"]["nwdp_cwc"]["records"] == 1

# whole orchestrator once (network blocked here => honest blockers, no crash)
rep = build_all.main(["--stages", "live"])
print("orchestrator blockers:", len(rep["blockers"]))
print("TEST ROOT (inspect if needed):", TMP)
print("ALL PIPELINE TESTS PASSED")
