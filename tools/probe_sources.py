"""Diagnostic probe (runs on a GitHub runner, which can reach servers the build sandbox cannot).
Honest identification, robots.txt respected, one request per URL, no retries, no workarounds. Writes diagnostics/probe.json."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.common import Http, utc_iso

http = Http(delay=1.5)
out = {"at": utc_iso(), "probes": []}


def probe(label, url, **kw):
    r = http.get(url, max_bytes=kw.pop("max_bytes", 4000), **kw)
    h = r.get("headers", {})
    rec = {"label": label, "url": url, "status": r.get("status"), "ok": r.get("ok"), "skipped": r.get("skipped"), "unreachable": r.get("unreachable"),
           "error": r.get("error", "")[:200], "content_type": h.get("Content-Type"), "content_length": h.get("Content-Length"), "server": h.get("Server"),
           "location": h.get("Location"), "body_head": r.get("content", b"")[:300].decode("utf-8", "ignore")}
    out["probes"].append(rec)
    print(label, rec["status"], rec["error"][:80], flush=True)
    return r


# 1. HydroRIVERS (HydroSHEDS) - what exactly is refused, and is there another official path?
for u in ["https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip",
          "https://data.hydrosheds.org/file/hydrorivers/HydroRIVERS_v10_as_shp.zip",
          "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as.gdb.zip",
          "https://www.hydrosheds.org/products/hydrorivers",
          "https://www.hydrosheds.org/hydrosheds-core-downloads",
          "https://data.hydrosheds.org/", "https://data.hydrosheds.org/file/HydroRIVERS/"]:
    probe("hydrosheds", u)
r = probe("hydrosheds-page", "https://www.hydrosheds.org/products/hydrorivers", max_bytes=400000)
out["hydrorivers_page_links"] = sorted(set(re.findall(r'https?://[^"\'\s<>]*(?:HydroRIVERS|hydrorivers)[^"\'\s<>]*', r.get("content", b"").decode("utf-8", "ignore"), flags=re.I)))[:40]

# 2. SACHET: what the CapFeed page really exposes
r = probe("sachet-capfeed", "https://sachet.ndma.gov.in/CapFeed", max_bytes=3_000_000)
html = r.get("content", b"").decode("utf-8", "ignore")
out["capfeed_hrefs"] = sorted(set(re.findall(r'(?:href|src|action)=["\']([^"\']+)["\']', html)))[:150]
out["capfeed_rss_mentions"] = sorted(set(re.findall(r'[^\s"\'<>]*(?:rss|\.xml|FetchXML)[^\s"\'<>]*', html, flags=re.I)))[:80]
for slug in ["himachal_pradesh", "himachal-pradesh", "himachalpradesh", "jammu_and_kashmir", "jammu_kashmir", "jammu-and-kashmir", "arunachal_pradesh",
             "arunachal-pradesh", "west_bengal", "west-bengal", "uttar_pradesh", "uttar-pradesh", "india", "all", "national"]:
    probe("sachet-feed-guess", f"https://sachet.ndma.gov.in/cap_public_website/rss/rss_{slug}.xml")

# 3. IMD public pages (no key used here)
for u in ["https://api.imd.gov.in/", "https://api.imd.gov.in/public/register.php", "https://mausam.imd.gov.in/"]:
    probe("imd", u)

# 4. Other candidate hosts for river-network topology / rasters (reachability only)
for u in ["https://overpass-api.de/api/status", "https://download.geofabrik.de/asia/india.html", "https://data.worldpop.org/GIS/Population/",
          "https://zenodo.org/", "https://services.arcgis.com/", "https://storage.googleapis.com/", "https://s3.amazonaws.com/",
          "https://huggingface.co/", "https://www.sciencebase.gov/", "https://figshare.com/"]:
    probe("reach", u)

Path("diagnostics").mkdir(exist_ok=True)
json.dump(out, open("diagnostics/probe.json", "w"), indent=1)
