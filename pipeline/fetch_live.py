"""Stage 5 - current / live sources. Nothing here is invented: if a feed cannot be read, the status says why.

  SACHET (NDMA)      official multi-agency CAP alerts -> web/data/live/alerts.json
  NWDP / CWC         telemetry catalogue and, where station coordinates exist, latest observations
  IMD API            needs IMD_API_KEY (environment variable); without it a clear blocker is recorded
Each source gets: status, fetched_at, data_date, record count, evidence label and any blocker text in
web/data/live/status.json, plus a provenance record.
"""
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon

from .common import (CACHE, INTERIM, WEB_DATA, WEB_LIVE, Http, ensure_dirs, load_config, log, normalise_name, read_json, record_source,
                     save_json, short, utc_iso, utc_now)


def strip_ns(tag):
    return tag.split("}", 1)[-1]


def parse_dt(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
    except Exception:
        pass
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


# --------------------------------------------------------------------------- SACHET
def parse_rss(content):
    root = ET.fromstring(content)
    items = []
    for el in root.iter():
        if strip_ns(el.tag) in ("item", "entry"):
            d = {}
            for c in el:
                t = strip_ns(c.tag)
                d[t] = (c.attrib.get("href") if t == "link" and c.attrib.get("href") else (c.text or "")).strip()
            items.append(d)
    return items


def parse_cap(content):
    """Parse one CAP 1.2 message into a flat dict (first <info> block plus all areas)."""
    root = ET.fromstring(content)
    out = {"identifier": None, "sender": None, "sent": None, "status": None, "msg_type": None, "areas": [], "polygons": [], "geocodes": []}
    info_seen = False
    for el in root.iter():
        t = strip_ns(el.tag)
        txt = (el.text or "").strip()
        if t in ("identifier", "sender", "sent", "status") and out.get(t) is None and txt:
            out[t] = txt
        elif t == "msgType" and txt and out["msg_type"] is None:
            out["msg_type"] = txt
        elif t == "info" and not info_seen:
            info_seen = True
            for c in el:
                ct, ctxt = strip_ns(c.tag), (c.text or "").strip()
                if ct in ("event", "urgency", "severity", "certainty", "effective", "onset", "expires", "headline", "description",
                          "instruction", "senderName", "category", "web") and ctxt:
                    out[ct] = ctxt
        elif t == "areaDesc" and txt:
            out["areas"].append(txt)
        elif t == "polygon" and txt:
            out["polygons"].append(txt)
        elif t == "geocode":
            vn = next((strip_ns(c.tag) for c in el if strip_ns(c.tag) == "valueName"), None)
            vals = {strip_ns(c.tag): (c.text or "").strip() for c in el}
            out["geocodes"].append({"name": vals.get("valueName"), "value": vals.get("value")})
    return out


def cap_polygon(text):
    pts = []
    for pair in text.split():
        try:
            a, b = pair.split(",")
            pts.append((float(b), float(a)))   # CAP is lat,lon -> shapely x=lon,y=lat
        except Exception:
            return None
    return Polygon(pts) if len(pts) >= 4 else None


def match_districts(alert, dists):
    """Match an alert to district ids. Method is recorded: POLYGON (geometric) or NAME_MATCH (text, lower confidence)."""
    if dists is None:
        return [], "NO_DISTRICT_LAYER"
    polys = [cap_polygon(p) for p in alert.get("polygons", [])]
    polys = [shapely.make_valid(p) for p in polys if p is not None]
    if polys:
        u = shapely.union_all(polys)
        hit = dists.sindex.query(u, predicate="intersects")
        # ignore slivers: keep districts where the overlap exceeds 5% of the district or of the alert polygon
        ids = []
        for h in hit:
            g = shapely.make_valid(dists.geometry.iloc[int(h)])
            inter = g.intersection(u).area
            if inter > 0.05 * min(g.area, u.area):
                ids.append(dists.iloc[int(h)]["did"])
        return ids, "POLYGON"
    names = {}
    for _, d in dists.iterrows():
        names.setdefault(normalise_name(d["name"]), []).append(d)
    ids = []
    for area in alert.get("areas", []):
        for piece in re.split(r"[;,/]|\band\b", area):
            key = normalise_name(piece)
            if key in names:
                ids += [r["did"] for r in names[key]]
    return sorted(set(ids)), ("NAME_MATCH" if ids else "UNMATCHED")


def sachet(http, cfg, dists, status):
    sc = cfg["sachet"]
    base = sc["base"].rstrip("/")
    entry = {"id": "sachet", "name": "NDMA SACHET (official CAP alerts)", "kind": "WARNING", "evidence": "OFFICIAL",
             "status": "NOT_RUN", "detail": "", "records": 0, "fetched_at": utc_iso(), "data_date": None, "blockers": []}
    status["sources"]["sachet"] = entry
    feeds = list(sc.get("feeds", []))
    if sc.get("discover_from_capfeed_page", True):
        r = http.get(base + "/CapFeed", max_bytes=3_000_000)
        if r["ok"] and r["status"] == 200:
            for href in re.findall(r'href=["\']([^"\']+)["\']', r["content"].decode("utf-8", "ignore"), flags=re.I):
                low = href.lower()
                if re.search(r"\.(png|jpg|gif|svg|css|js|pdf)(\?|$)", low):
                    continue
                if low.endswith(".xml") or "/rss" in low:
                    feeds.append(urljoin(base + "/CapFeed", href))
        elif r["skipped"] or r["unreachable"]:
            entry["blockers"].append("SACHET feed page not reachable: " + r["error"])
    if sc.get("try_state_pattern_guesses", True) and not sc.get("feeds"):
        for slug in sc.get("state_slugs", []):
            feeds.append(f"{base}/cap_public_website/rss/rss_{slug}.xml")
    feeds = list(dict.fromkeys(feeds))
    etags = read_json(CACHE / "sachet_etags.json", {})
    cap_cache = read_json(CACHE / "sachet_cap_cache.json", {})
    items_all, feed_ok, feeds_failed = [], [], []
    for f in feeds:
        r = http.get(f, etag=etags.get(f), max_bytes=10_000_000)
        if r["skipped"] or r["unreachable"]:
            entry["blockers"].append(f"{f}: {r['error']}")
            if r["unreachable"]:
                break
            continue
        if r["status"] == 304:
            feed_ok.append(f)
            continue
        if r["status"] != 200:
            feeds_failed.append({"feed": f, "http": r["status"]})
            if r["status"] in (401, 403):
                entry["blockers"].append(f"{f}: HTTP {r['status']} (login or firewall; not worked around)")
            continue
        try:
            items = parse_rss(r["content"])
        except ET.ParseError:
            feeds_failed.append({"feed": f, "http": r["status"], "error": "not valid RSS/XML"})
            continue
        feed_ok.append(f)
        if r["headers"].get("ETag"):
            etags[f] = r["headers"]["ETag"]
        for it in items:
            it["_feed"] = f
        items_all += items
    save_json(CACHE / "sachet_etags.json", etags)
    # fetch each CAP file once (cached by link)
    fetched = 0
    cap_deadline = time.monotonic() + sc.get("budget_seconds", 420)
    cap_skipped = 0
    now = utc_now()
    alerts = []
    seen_ids = set()
    for it in items_all:
        link = it.get("link", "")
        if not link or urlparse(link).netloc != urlparse(base).netloc:
            continue
        cap = cap_cache.get(link)
        if cap is None and time.monotonic() > cap_deadline:
            cap_skipped += 1
            continue
        if cap is None and fetched < sc.get("max_cap_files_per_run", 400):
            r = http.get(link, max_bytes=2_000_000)
            fetched += 1
            if r["ok"] and r["status"] == 200:
                try:
                    cap = parse_cap(r["content"])
                    cap_cache[link] = cap
                except ET.ParseError:
                    cap = None
            elif r["unreachable"]:
                entry["blockers"].append("CAP file fetch stopped: " + r["error"])
                break
        if not cap or cap.get("identifier") in seen_ids:
            continue
        seen_ids.add(cap.get("identifier"))
        exp = parse_dt(cap.get("expires"))
        sent = parse_dt(cap.get("sent"))
        if exp and exp < now:
            continue                       # expired: not shown as current
        if not exp and sent and now - sent > timedelta(hours=48):
            continue                       # no expiry given and older than 48 h
        ids, method = match_districts(cap, dists)
        alerts.append({
            "id": cap.get("identifier"), "sender": cap.get("senderName") or cap.get("sender"), "sender_id": cap.get("sender"),
            "event": cap.get("event"), "headline": cap.get("headline"), "severity": cap.get("severity"), "urgency": cap.get("urgency"),
            "certainty": cap.get("certainty"), "sent": cap.get("sent"), "effective": cap.get("effective"), "expires": cap.get("expires"),
            "description": (cap.get("description") or "")[:600], "instruction": (cap.get("instruction") or "")[:400],
            "areas": cap.get("areas", [])[:12], "districts": ids[:60], "match_method": method, "feed": it.get("_feed"), "cap_url": link,
            "kind": "WARNING", "issued_by_official_agency": True})
    if cap_skipped:
        entry["blockers"].append(f"SACHET time budget reached: {cap_skipped} CAP file(s) not fetched this run; they are retried next run (some current alerts may be missing until then)")
    # prune cache so it does not grow forever
    keep = {it.get("link") for it in items_all}
    save_json(CACHE / "sachet_cap_cache.json", {k: v for k, v in cap_cache.items() if k in keep})
    dates = [parse_dt(a["sent"]) for a in alerts if a.get("sent")]
    entry.update(records=len(alerts), feeds_ok=feed_ok, feeds_failed=feeds_failed, data_date=(max(dates).isoformat() if dates else None))
    if not feed_ok:
        entry.update(status="BLOCKED", detail="No SACHET feed could be read. Add working feed URLs from your endpoint test to config/live_sources.json (sachet.feeds).")
    elif not alerts:
        entry.update(status="NO_CURRENT_ALERTS", detail=f"{len(feed_ok)} feed(s) read; no unexpired alerts found. This is not an error.")
    else:
        entry.update(status="OK", detail=f"{len(alerts)} current alert(s) from {len(feed_ok)} feed(s)")
    save_json(WEB_LIVE / "alerts.json", {"generated_at": utc_iso(), "source": "NDMA SACHET (aggregates IMD, CWC, GSI, INCOIS and others)",
                                         "kind": "WARNING", "feeds": feed_ok, "alerts": alerts})
    record_source(dict(source_id="sachet", dataset_id="sachet", title="NDMA SACHET CAP alerts", organisation="National Disaster Management Authority",
                       authority="indian_government", landing_page=base, url=feed_ok[0] if feed_ok else base, retrieved_at=utc_iso(),
                       data_date=entry["data_date"], kind="WARNING", status=entry["status"], note=entry["detail"],
                       licence="Check NDMA terms; attribution to the issuing agency shown with every alert",
                       limitations="District matching is by CAP polygon where given, otherwise by name (lower confidence). Alerts are official warnings, not SHAILDHARA forecasts."))
    log(f"sachet: {entry['status']} - {entry['detail']}")


# --------------------------------------------------------------------------- NWDP / CWC
LAT = re.compile(r"^(lat|latitude|y_coord|lat_dd)$", re.I)
LON = re.compile(r"^(lon|long|longitude|lng|x_coord|lon_dd)$", re.I)
STN = re.compile(r"(station.*(name|id|code)|^station$|site.*name)", re.I)
TIME = re.compile(r"(date|time|timestamp|observed)", re.I)
VAL = re.compile(r"(water.?level|^level$|discharge|rainfall|^value$|stage|snow)", re.I)   # kept for reference
UNIT = re.compile(r"\(([^)]*)\)\s*$")
SKIP_VAL = re.compile(r"^(is_|rl_|meansea|slno|area|velocity)|code$", re.I)


def valid_obs(o):
    """A station reading is usable only with finite coordinates inside India's bounding box and a finite value (NaN is not a reading)."""
    try:
        lat, lon, v = float(o["lat"]), float(o["lon"]), float(o["value"])
    except (TypeError, ValueError, KeyError):
        return False
    return all(map(math.isfinite, (lat, lon, v))) and 6 <= lat <= 38 and 67 <= lon <= 98.5


def pick_fields(fields):
    """Explicit rules for the NWDP telemetry schemas seen in practice (checked against the real portal on 2026-10-08).
    The measurement is the column that carries a unit in brackets, e.g. 'River Water Level Telemetry Hourly (meter)';
    flags such as Is_DischargeDataAvailable, RL_of_zeroGauge, MeanSeaLevel, Area, Velocity are never used as the value."""
    lat = next((f for f in fields if LAT.match(f)), None)
    lon = next((f for f in fields if LON.match(f)), None)
    stn = (next((f for f in fields if re.fullmatch(r"station", f, re.I)), None) or next((f for f in fields if re.fullmatch(r"location name", f, re.I)), None)
           or next((f for f in fields if STN.search(f)), None))
    tm = (next((f for f in fields if re.fullmatch(r"data acquisition time|monitoring date", f, re.I)), None) or next((f for f in fields if TIME.search(f)), None))
    cands = [f for f in fields if f not in (lat, lon, stn, tm) and not SKIP_VAL.search(f) and (UNIT.search(f) or re.fullmatch(r"water discharge", f, re.I))]
    return lat, lon, stn, tm, (cands[-1] if cands else None)



def nwdp(http, cfg, status):
    nc = cfg["nwdp"]
    host = nc["host"].rstrip("/")
    entry = {"id": "nwdp_cwc", "name": "NWDP / CWC telemetry datasets", "kind": "OBSERVED", "evidence": "OFFICIAL",
             "status": "NOT_RUN", "detail": "", "records": 0, "fetched_at": utc_iso(), "data_date": None, "blockers": []}
    status["sources"]["nwdp_cwc"] = entry
    catalogue, obs = {}, []
    fresh_cut = (utc_now() - timedelta(days=nc.get("fresh_days", 14))).date()
    for q in nc.get("queries", []):
        r = http.get(host + "/api/3/action/package_search", params={"q": q, "rows": 20}, max_bytes=5_000_000)
        if r["skipped"] or r["unreachable"]:
            entry["blockers"].append(f"{q}: {r['error']}")
            if r["unreachable"]:
                break
            continue
        if r["status"] != 200:
            entry["blockers"].append(f"{q}: HTTP {r['status']}")
            continue
        try:
            data = json.loads(r["content"].decode("utf-8", "ignore"))
        except Exception:
            entry["blockers"].append(f"{q}: response was not JSON (CKAN API may live at a different path)")
            continue
        if not data.get("success"):
            continue
        for ds in data["result"].get("results", []):
            md = parse_dt(ds.get("metadata_modified"))
            org = (ds.get("organization") or {}).get("title") or ""
            catalogue[ds["id"]] = {
                "id": ds["id"], "name": ds.get("name"), "title": ds.get("title"), "organisation": org, "modified": ds.get("metadata_modified"),
                "fresh": bool(md and md.date() >= fresh_cut), "url": f"{host}/dataset/{ds.get('name')}",
                "resources": [{"id": x.get("id"), "format": x.get("format"), "datastore": bool(x.get("datastore_active"))} for x in ds.get("resources", [])]}
    # try latest rows of fresh datastore-enabled resources (each resource isolated: one bad schema never stops the rest)
    schemas = []
    prev = read_json(WEB_LIVE / "river_observations.json", {}) or {}
    prev_obs = prev.get("observations", []) if isinstance(prev, dict) else []
    productive = {o.get("dataset_id") for o in prev_obs}
    work = [(ds, res) for ds in catalogue.values() if ds["fresh"] for res in ds["resources"] if res["datastore"]]
    first = [w for w in work if w[0]["id"] in productive]            # datasets that gave station readings last time come first
    rest = sorted((w for w in work if w[0]["id"] not in productive), key=lambda w: w[1]["id"] or "")
    if rest:                                                          # rotate the rest so every dataset is re-checked over a few runs
        k = int(utc_now().timestamp() // 10800) * 40 % len(rest)
        rest = rest[k:] + rest[:k]
    deadline = time.monotonic() + nc.get("budget_seconds", 600)
    not_reached = 0
    refreshed = set()
    for ds, res in first + rest:
        if True:
            if time.monotonic() > deadline:
                not_reached += 1
                res["schema_note"] = "not checked this run (time budget); earlier readings reused if still recent"
                continue
            refreshed.add(ds["id"])
            try:
                limit = nc.get("max_rows_per_resource", 2000)
                r = http.get(host + "/api/3/action/datastore_search", params={"resource_id": res["id"], "limit": limit}, max_bytes=15_000_000)
                if not (r["ok"] and r["status"] == 200):
                    res["schema_note"] = f"datastore_search HTTP {r.get('status')}"
                    continue
                d = json.loads(r["content"].decode("utf-8", "ignore"))["result"]
                fields = [f["id"] for f in d.get("fields", []) if f["id"] != "_id"]
                lat, lon, stn, tm, vl = pick_fields(fields)
                recs = d.get("records", []) or []
                res["schema"] = {"lat": lat, "lon": lon, "station": stn, "time": tm, "value": vl}
                schemas.append({"dataset": ds["title"], "resource": res["id"], "fields": fields[:30], "schema": res["schema"],
                                "rows_returned": len(recs), "total_rows": d.get("total"), "first_record_keys": list(recs[0].keys())[:30] if recs else []})
                if not (lat and lon and stn and tm and vl):
                    res["schema_note"] = "fields not recognised as station + coordinates + time + value"
                    continue
                if not recs:
                    res["schema_note"] = "datastore returned no rows"
                    continue
                if isinstance(d.get("total"), int) and d["total"] > len(recs):
                    # more rows exist than one page: ask for the newest rows first
                    r2 = http.get(host + "/api/3/action/datastore_search", params={"resource_id": res["id"], "limit": limit, "sort": f'"{tm}" desc'}, max_bytes=15_000_000)
                    if r2["ok"] and r2["status"] == 200:
                        recs = json.loads(r2["content"].decode("utf-8", "ignore"))["result"].get("records", []) or recs
                df = pd.DataFrame(recs)
                missing = [c for c in (lat, lon, stn, tm, vl) if c not in df.columns]
                if df.empty or missing:
                    res["schema_note"] = f"declared fields missing from records: {missing}"
                    continue
                df["_t"] = pd.to_datetime(df[tm], errors="coerce", utc=True)
                now_ts = pd.Timestamp(utc_now())
                cut = now_ts - pd.Timedelta(days=nc.get("max_obs_age_days", 7))
                df = df.dropna(subset=["_t"])
                n_all = len(df)
                df = df[(df["_t"] >= cut) & (df["_t"] <= now_ts + pd.Timedelta(days=1))]     # only recent readings; never a stale or future-dated one
                res["rows_dropped_not_recent"] = n_all - len(df)
                df = df.sort_values("_t").groupby(stn).tail(1)
                um = UNIT.search(vl)
                n_invalid = 0
                for _, row in df.iterrows():
                    try:
                        o = {"station": str(row[stn]), "lat": float(row[lat]), "lon": float(row[lon]), "parameter": vl,
                             "value": float(row[vl]), "observed_at": row["_t"].isoformat(), "dataset": ds["title"], "dataset_id": ds["id"],
                             "unit": um.group(1) if um else None, "kind": "OBSERVED"}
                    except Exception:
                        n_invalid += 1
                        continue
                    if valid_obs(o):
                        obs.append(o)
                    else:
                        n_invalid += 1                      # NaN / missing coordinates or value: not a reading, never plotted
                res["rows_dropped_invalid"] = n_invalid
            except Exception as e:
                res["schema_note"] = "failed: " + short(e, 160)
                entry["blockers"].append(f"{ds.get('title')}: {short(e, 160)}")
    if not_reached:
        # reuse earlier readings only for datasets not re-read this run, and only while they are still inside the recency window
        have = {(o["dataset_id"], o["station"], o["parameter"]) for o in obs}
        cutoff = utc_now() - timedelta(days=nc.get("max_obs_age_days", 7))
        kept = 0
        for o in prev_obs:
            t = parse_dt(o.get("observed_at"))
            if not valid_obs(o) or o.get("dataset_id") in refreshed or not t or t < cutoff or (o.get("dataset_id"), o.get("station"), o.get("parameter")) in have:
                continue
            obs.append(o)
            kept += 1
        entry["blockers"].append(f"NWDP time budget reached: {not_reached} dataset resource(s) not re-read this run; {kept} earlier reading(s) kept with their original observation times")
    dates = [pd.Timestamp(o["observed_at"]) for o in obs]
    entry.update(records=len(obs), data_date=(max(dates).isoformat() if dates else None))
    n_fresh = sum(1 for c in catalogue.values() if c["fresh"])
    if not catalogue:
        entry.update(status="BLOCKED", detail="NWDP search API returned nothing usable. " + ("; ".join(entry["blockers"][:2]) or ""))
    elif obs:
        entry.update(status="OK", detail=f"{len(obs)} station observation(s) from {n_fresh} recently updated dataset(s)")
    else:
        entry.update(status="CATALOGUE_ONLY",
                     detail=f"{len(catalogue)} dataset(s) found, {n_fresh} updated in the last {nc.get('fresh_days', 14)} days, but no station observations could be extracted automatically (schema not recognised or not queryable). See the catalogue.")
    save_json(WEB_LIVE / "nwdp_catalogue.json", {"generated_at": utc_iso(), "datasets": list(catalogue.values()), "schemas_checked": schemas})
    save_json(WEB_LIVE / "river_observations.json", {"generated_at": utc_iso(), "kind": "OBSERVED", "observations": obs})
    record_source(dict(source_id="nwdp_cwc", dataset_id="nwdp_cwc", title="NWDP/CWC telemetry catalogue", organisation="NWIC / Central Water Commission",
                       authority="indian_government", landing_page=host, url=host + "/api/3/action/package_search", retrieved_at=utc_iso(),
                       data_date=entry["data_date"], kind="OBSERVED", status=entry["status"], note=entry["detail"],
                       licence="Portal copyright policy: acknowledge source; check dataset licence",
                       limitations="Station schemas vary by dataset; only datasets with recognisable station, coordinates, time and value fields are plotted. Classified-basin data may need permission."))
    log(f"nwdp: {entry['status']} - {entry['detail']}")


# --------------------------------------------------------------------------- IMD
def imd(http, cfg, dists, status):
    ic = cfg["imd"]
    entry = {"id": "imd", "name": "IMD API (district warnings, rainfall, nowcast, basin QPF)", "kind": "WARNING/FORECAST", "evidence": "OFFICIAL",
             "status": "NOT_RUN", "detail": "", "records": 0, "fetched_at": utc_iso(), "data_date": None, "blockers": []}
    status["sources"]["imd"] = entry
    key = os.environ.get("IMD_API_KEY", "").strip()
    if not ic.get("enabled", True):
        entry.update(status="DISABLED", detail="Disabled in config/live_sources.json")
        return
    if not key:
        entry.update(status="NEEDS_API_KEY", detail="IMD_API_KEY is not set. Register at https://api.imd.gov.in/public/register.php, then set the key as an environment variable / secret. IMD also describes an IP-whitelisting step.")
        entry["blockers"].append("IMD API key required (and possibly IP whitelisting by IMD)")
        log("imd: NEEDS_API_KEY")
        return
    headers = {ic["auth_header_name"]: ic["auth_header_prefix"] + key}
    got = 0
    for name, ep in ic["endpoints"].items():
        r = http.get(ic["base"] + ep, headers=headers, max_bytes=20_000_000)
        if r["skipped"] or r["unreachable"]:
            entry["blockers"].append(f"{name}: {r['error']}")
            continue
        if r["status"] in (401, 403):
            entry["blockers"].append(f"{name}: HTTP {r['status']} - key rejected, wrong header style, or this machine is not whitelisted by IMD")
            continue
        if r["status"] != 200:
            entry["blockers"].append(f"{name}: HTTP {r['status']}")
            continue
        try:
            data = json.loads(r["content"].decode("utf-8", "ignore"))
        except Exception:
            entry["blockers"].append(f"{name}: not JSON")
            continue
        rows = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else [data])
        # Raw rows are kept untouched. Colour/level codes are NOT translated: the numeric code tables differ between IMD endpoints.
        matched = []
        if dists is not None:
            idx = {}
            for _, d in dists.iterrows():
                idx.setdefault(normalise_name(d["name"]), []).append(d["did"])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                nm = next((str(v) for k, v in row.items() if re.search(r"district", k, re.I) and v), None)
                matched.append({"district_ids": idx.get(normalise_name(nm), []) if nm else [], "raw": row})
        save_json(WEB_LIVE / f"imd_{name}.json", {"generated_at": utc_iso(), "endpoint": ep, "kind": "WARNING/FORECAST", "rows": matched or rows,
                                                  "note": "Raw IMD values. Code-to-colour mapping is not applied; consult IMD's API reference for the table of this endpoint."})
        got += 1
        entry["records"] += len(rows)
    entry.update(status="OK" if got else "ERROR", detail=f"{got} of {len(ic['endpoints'])} endpoints read")
    record_source(dict(source_id="imd", dataset_id="imd", title="IMD API", organisation="India Meteorological Department", authority="indian_government",
                       landing_page="https://api.imd.gov.in/", url=ic["base"], retrieved_at=utc_iso(), kind="WARNING/FORECAST", status=entry["status"],
                       note=entry["detail"], licence="IMD terms; attribute IMD; client-side caching requested",
                       limitations="Official IMD forecasts/warnings shown as issued. Not SHAILDHARA predictions."))
    log(f"imd: {entry['status']} - {entry['detail']}")


def imd_geoserver(http, cfg, status):
    gc = cfg.get("imd_geoserver", {})
    entry = {"id": "imd_geoserver", "name": "IMD public district-warning map service (undocumented)", "kind": "WARNING", "evidence": "OFFICIAL",
             "status": "DISABLED", "detail": gc.get("notes", ""), "records": 0, "fetched_at": utc_iso(), "data_date": None, "blockers": []}
    status["sources"]["imd_geoserver"] = entry
    if not gc.get("enabled"):
        return
    r = http.get(gc["url"], params={"service": "WFS", "version": "1.1.0", "request": "GetFeature", "typename": gc["typename"],
                                    "srsname": "EPSG:4326", "outputFormat": "application/json"}, max_bytes=40_000_000)
    if not (r["ok"] and r["status"] == 200):
        entry.update(status="BLOCKED", detail=r.get("error") or f"HTTP {r['status']}")
        return
    try:
        fc = json.loads(r["content"].decode("utf-8", "ignore"))
        save_json(WEB_LIVE / "imd_geoserver_raw.json", {"generated_at": utc_iso(), "features": [f.get("properties") for f in fc.get("features", [])][:2000]})
        entry.update(status="OK", records=len(fc.get("features", [])), detail="Raw feature properties saved; not interpreted")
    except Exception as e:
        entry.update(status="ERROR", detail=short(e))


def load_districts():
    """District polygons for matching: cached full layer if present, else the published (simplified) web layer."""
    p = INTERIM / "foundation" / "admin_district.pkl"
    if p.exists():
        return pd.read_pickle(p)
    g = WEB_DATA / "districts.geojson"
    if g.exists():
        import geopandas as gpd
        gdf = gpd.read_file(g, engine="pyogrio")
        if {"did", "name"} <= set(gdf.columns):
            return gdf.reset_index(drop=True)
    return None


# --------------------------------------------------------------------------- orchestrator
def run():
    ensure_dirs()
    cfg = load_config("live_sources.json")
    http = Http()
    status = {"generated_at": utc_iso(), "sources": {}}
    dists = load_districts()
    if dists is None:
        log("live: district layer not built yet; alerts will not be matched to districts")
    for fn, args in ((sachet, (http, cfg, dists, status)), (nwdp, (http, cfg, status)), (imd, (http, cfg, dists, status)),
                     (imd_geoserver, (http, cfg, status))):
        try:
            fn(*args)
        except Exception as e:
            sid = fn.__name__
            status["sources"][sid] = {"id": sid, "status": "ERROR", "detail": short(e), "records": 0, "fetched_at": utc_iso()}
            log(f"live: {sid} ERROR {short(e)}")
    save_json(WEB_LIVE / "status.json", status, compact=False)
    return status
