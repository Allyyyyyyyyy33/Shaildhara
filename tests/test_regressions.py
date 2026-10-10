"""TEST ONLY. Regression tests for failures seen in the first real GitHub run (fictional fixtures, temporary root).

    python tests/test_regressions.py
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="shd_reg_"))
shutil.copytree(PROJECT / "config", TMP / "config")
(TMP / "web").mkdir()
os.environ["SHAILDHARA_ROOT"] = str(TMP)
os.environ["SHAILDHARA_DELAY"] = "0"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

from pyproj import Transformer
from pipeline import build_foundation, fetch_live
from pipeline.common import INTERIM, MANUAL, RAW, WEB_LIVE, ensure_dirs
import stage_release_files

ensure_dirs()
ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1
    print("ok  :", msg)


# 1. projected GeoJSON with no CRS member (what NWDP serves) is detected, inferred and reprojected
t = Transformer.from_crs("EPSG:4326", "EPSG:7755", always_xy=True)
ring = [t.transform(x, y) for x, y in [(78, 30), (79, 30), (79, 31), (78, 31), (78, 30)]]
fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
f = TMP / "projected.geojson"
f.write_text(json.dumps(fc))
gdf, meta = build_foundation.read_vector(f)
b = gdf.total_bounds
check(77.9 < b[0] < 78.1 and 29.9 < b[1] < 30.1 and 78.9 < b[2] < 79.1, f"projected GeoJSON reprojected to degrees {list(map(lambda x: round(float(x), 2), b))}")
check("INFERRED" in meta["crs_original"] and meta["notes"], "inference is recorded as an assumption in the audit notes")
# coordinates that fit nowhere in India are refused, not guessed
bad = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [250000000, 1]}}]}
(TMP / "bad.geojson").write_text(json.dumps(bad))
try:
    build_foundation.read_vector(TMP / "bad.geojson")
    check(False, "nonsense coordinates must be refused")
except ValueError:
    check(True, "coordinates that fit no candidate CRS are refused")
# normal degrees are untouched
ok_fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [78.5, 30.2]}}]}
(TMP / "deg.geojson").write_text(json.dumps(ok_fc))
g2, m2 = build_foundation.read_vector(TMP / "deg.geojson")
check(abs(g2.geometry.iloc[0].x - 78.5) < 1e-9 and "INFERRED" not in m2["crs_original"], "ordinary lat/lon GeoJSON is left alone")

# 2. ZIP layouts: upper-case extension in a sub-folder, nested ZIP, and a ZIP with no vectors (diagnostics kept)
(RAW / "admin_state").mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(RAW / "admin_state" / "state_nwic_geojson.zip", "w") as z:
    z.writestr("STATES/INDIA_STATE.GEOJSON", json.dumps(ok_fc))
v = build_foundation.find_vectors("admin_state")
check(len(v) == 1 and v[0].name == "INDIA_STATE.GEOJSON", "upper-case .GEOJSON inside a sub-folder is found")
(RAW / "admin_district").mkdir(parents=True, exist_ok=True)
inner = TMP / "inner.zip"
with zipfile.ZipFile(inner, "w") as z:
    z.writestr("d.geojson", json.dumps(ok_fc))
with zipfile.ZipFile(RAW / "admin_district" / "district_nwic_geojson.zip", "w") as z:
    z.write(inner, "wrapped/inner.zip")
v = build_foundation.find_vectors("admin_district")
check(len(v) == 1 and v[0].name == "d.geojson", "a ZIP inside a ZIP is unpacked")
(RAW / "glacial_lakes_cwc").mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(RAW / "glacial_lakes_cwc" / "x.zip", "w") as z:
    z.writestr("readme.txt", "no data here")
    z.writestr("sub/table.csv", "a,b")
v = build_foundation.find_vectors("glacial_lakes_cwc")
check(v == [] and "readme.txt" in build_foundation.ZIP_DIAG["glacial_lakes_cwc"], "a ZIP with no vectors says exactly what it held")

# 3. fake HTTP
class FakeHttp:
    def __init__(self, routes): self.routes = routes
    def get(self, url, params=None, headers=None, etag=None, max_bytes=0, timeout=None):
        for k, v in self.routes.items():
            if url.startswith(k):
                if callable(v): v = v(url, params)
                return {"ok": True, "skipped": False, "unreachable": False, "status": v[0], "headers": v[2] if len(v) > 2 else {}, "content": v[1], "error": "", "truncated": False}
        return {"ok": False, "skipped": False, "unreachable": True, "status": None, "headers": {}, "content": b"", "error": "no route (test)"}

from datetime import datetime, timezone
today = datetime.now(timezone.utc).isoformat()
search = json.dumps({"success": True, "result": {"results": [
    {"id": "d1", "name": "empty-rows", "title": "Declared but empty", "metadata_modified": today, "organization": {"title": "CWC"}, "resources": [{"id": "r1", "format": "CSV", "datastore_active": True}]},
    {"id": "d2", "name": "good", "title": "Good telemetry", "metadata_modified": today, "organization": {"title": "CWC"}, "resources": [{"id": "r2", "format": "CSV", "datastore_active": True}]}]}}).encode()
fields = [{"id": "_id"}, {"id": "Station"}, {"id": "Latitude"}, {"id": "Longitude"}, {"id": "Is_DischargeDataAvailable"}, {"id": "RL_of_zeroGauge"},
          {"id": "Data Acquisition Time"}, {"id": "River Water Level Telemetry Hourly (meter)"}]


def ds_route(url, params):
    if params["resource_id"] == "r1":
        return (200, json.dumps({"success": True, "result": {"fields": fields, "records": [], "total": 0}}).encode())
    recs = [{"Station": "S1", "Latitude": 29.1, "Longitude": 78.1, "Is_DischargeDataAvailable": 1, "RL_of_zeroGauge": 50.0, "Data Acquisition Time": today, "River Water Level Telemetry Hourly (meter)": 101.5},
            {"Station": "S2", "Latitude": 29.2, "Longitude": 78.2, "Is_DischargeDataAvailable": 0, "RL_of_zeroGauge": 40.0, "Data Acquisition Time": "2000-01-01T01:50:00", "River Water Level Telemetry Hourly (meter)": 7.0}]
    return (200, json.dumps({"success": True, "result": {"fields": fields, "records": recs, "total": 1}}).encode())


cfg = json.load(open(PROJECT / "config" / "live_sources.json"))
cfg["nwdp"].update(host="https://nwdp.test", queries=["x"])
status = {"sources": {}}
fetch_live.nwdp(FakeHttp({"https://nwdp.test/api/3/action/package_search": (200, search), "https://nwdp.test/api/3/action/datastore_search": ds_route}), cfg, status)
e = status["sources"]["nwdp_cwc"]
check(e["status"] == "OK" and e["records"] == 1, f"NWDP: an empty datastore no longer crashes the stage; only the recent reading is kept ({e['status']}, {e['records']})")
ob = json.load(open(WEB_LIVE / "river_observations.json"))["observations"]
check(ob[0]["value"] == 101.5 and ob[0]["unit"] == "meter" and "Is_Discharge" not in ob[0]["parameter"], "NWDP: the value is the measurement column, never a flag such as Is_DischargeDataAvailable")
check(all(o["station"] != "S2" for o in ob), "NWDP: a reading dated 2000-01-01 is not presented as current")
cat = json.load(open(WEB_LIVE / "nwdp_catalogue.json"))
check(len(cat["schemas_checked"]) == 2 and any(r.get("schema_note") for d in cat["datasets"] for r in d["resources"]), "NWDP: schemas checked are recorded for diagnosis")

# 4. SACHET: a failing state feed is recorded with its HTTP status
RSS = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
cfg["sachet"].update(base="https://sachet.test", feeds=["https://sachet.test/rss/ok.xml", "https://sachet.test/rss/gone.xml"], discover_from_capfeed_page=False, try_state_pattern_guesses=False)
status = {"sources": {}}
fetch_live.sachet(FakeHttp({"https://sachet.test/rss/ok.xml": (200, RSS), "https://sachet.test/rss/gone.xml": (404, b"")}), cfg, None, status)
s = status["sources"]["sachet"]
check(s["feeds_failed"] == [{"feed": "https://sachet.test/rss/gone.xml", "http": 404}], "SACHET: failed feeds are listed with their HTTP status")

# 5. release file routing
src = TMP / "rel"
src.mkdir()
for n in ("HydroRIVERS_v10_as_shp.zip", "ind_ppp_2020_1km_Aggregated.tif", "ecosystems__protected.zip", "mystery.bin"):
    (src / n).write_bytes(b"x")
moved = stage_release_files.main(src, TMP / "manual_out")
check(sorted(moved) == ["ecosystems", "hydrorivers_asia", "worldpop_india"] and (TMP / "manual_out" / "hydrorivers_asia" / "HydroRIVERS_v10_as_shp.zip").exists()
      and (src / "mystery.bin").exists(), "release files are routed by name; unknown files are left alone")

# 14. live refresh stays inside its time budget and never re-reads everything (the 30-minute overrun)
from datetime import datetime, timedelta, timezone
from pipeline import build_all
check(not hasattr(build_all, "build_exposure") and not hasattr(build_all, "fetch_static"), "build_all does not import the static-build stages at load time")
_now = datetime.now(timezone.utc)
_res = [{"id": f"r{i:03d}", "format": "CSV", "datastore_active": True} for i in range(60)]
_search = json.dumps({"success": True, "result": {"results": [{"id": "dBIG", "name": "big", "title": "Big", "metadata_modified": _now.isoformat(),
                      "organization": {"title": "CWC"}, "resources": _res}]}}).encode()
_calls = {"n": 0}
class _Counting:
    def get(self, url, params=None, **kw):
        if "package_search" in url:
            return {"ok": True, "skipped": False, "unreachable": False, "status": 200, "headers": {}, "content": _search, "error": "", "truncated": False}
        _calls["n"] += 1
        return {"ok": True, "skipped": False, "unreachable": False, "status": 500, "headers": {}, "content": b"", "error": "", "truncated": False}
json.dump({"observations": [
    {"station": "S1", "lat": 29.1, "lon": 78.1, "parameter": "Level (meter)", "value": 1.0, "observed_at": (_now - timedelta(days=1)).isoformat(), "dataset": "Big", "dataset_id": "dBIG", "unit": "meter", "kind": "OBSERVED"},
    {"station": "S2", "lat": 29.2, "lon": 78.2, "parameter": "Level (meter)", "value": 2.0, "observed_at": (_now - timedelta(days=30)).isoformat(), "dataset": "Big", "dataset_id": "dBIG", "unit": "meter", "kind": "OBSERVED"}]},
    open(WEB_LIVE / "river_observations.json", "w"))
_cfg = json.load(open(PROJECT / "config" / "live_sources.json"))
_cfg["nwdp"].update(host="https://nwdp.budget.test", queries=["q"], budget_seconds=0)
_st = {"sources": {}}
fetch_live.nwdp(_Counting(), _cfg, _st)
_obs = json.load(open(WEB_LIVE / "river_observations.json"))["observations"]
check(_calls["n"] == 0, "NWDP with an exhausted time budget makes no further per-resource requests")
check([o["station"] for o in _obs] == ["S1"], "earlier reading kept only while recent; 30-day-old reading dropped")
check(any("time budget" in b for b in _st["sources"]["nwdp_cwc"]["blockers"]), "budget cut-off is reported openly in the source status")
check(_obs[0]["observed_at"] == (_now - timedelta(days=1)).isoformat(), "carried-over reading keeps its original observation time")
print(f"ALL REGRESSION TESTS PASSED ({ok})")
