"""Stage 1 - fetch static datasets (raw files stay unchanged) and record provenance."""
from pathlib import Path

from .common import (MANUAL, RAW, Http, load_config, log, record_source, sha256_file, short, utc_iso, ensure_dirs)


def _manual_files(ds_id):
    folder = MANUAL / ds_id
    if not folder.exists():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.name != ".gitkeep")


def _provenance(ds, path, method, url, url_status, note=""):
    p = Path(path)
    record_source(dict(
        source_id=f"{ds['id']}::{p.name}", dataset_id=ds["id"], title=ds["title"], organisation=ds["organisation"],
        authority=ds.get("authority"), landing_page=ds.get("landing_page"), url=url, url_status=url_status,
        format=ds.get("format"), data_date=ds.get("data_date"), retrieved_at=utc_iso(), retrieval_method=method,
        sha256=sha256_file(p), size_bytes=p.stat().st_size, coverage=ds.get("coverage"), licence=ds.get("licence"),
        limitations=ds.get("limitations"), kind="STATIC", status="OK", note=note))


def run(heavy=False, force=False, only=None):
    ensure_dirs()
    cfg = load_config("sources.json")
    http = Http()
    report = {"started_at": utc_iso(), "datasets": {}}
    for ds in cfg["static"]:
        ds_id = ds["id"]
        if only and ds_id not in only:
            continue
        entry = {"status": "NOT_RUN", "detail": ""}
        report["datasets"][ds_id] = entry
        # 1) files you placed by hand always win
        mf = _manual_files(ds_id)
        if mf:
            for p in mf:
                _provenance(ds, p, "manual_placement", ds.get("landing_page", ""), "n/a (placed by hand)",
                            "timestamp is when the pipeline recorded it, not when you downloaded it")
            entry.update(status="OK_MANUAL", detail=f"{len(mf)} file(s) in data/manual/{ds_id}/")
            log(f"{ds_id}: using {len(mf)} manual file(s)")
            continue
        if not ds.get("urls"):
            entry.update(status="BLOCKED_MANUAL", detail=f"No verified programmatic URL. Place file(s) in data/manual/{ds_id}/ ({ds.get('limitations','')})")
            log(f"{ds_id}: needs manual file(s)")
            continue
        if ds.get("heavy") and not heavy:
            entry.update(status="SKIPPED_HEAVY", detail="Large download; run with --heavy to include it")
            log(f"{ds_id}: skipped (heavy)")
            continue
        folder = RAW / ds_id
        dest = folder / ds["filename"]
        if dest.exists() and not force:
            for u in ds["urls"][:1]:
                _provenance(ds, dest, "download (file reused from an earlier run)", u["url"], u["status"] + " (earlier run)")
            entry.update(status="OK_REUSED", detail=str(dest))
            log(f"{ds_id}: reusing {dest.name}")
            continue
        last_err = ""
        for u in ds["urls"]:
            log(f"{ds_id}: downloading ({u['status']} URL) ...")
            r = http.download(u["url"], dest)
            if r["ok"]:
                _provenance(ds, dest, "http_download", u["url"], f"{u['status']} -> download OK (HTTP {r['status']})")
                entry.update(status="OK", detail=f"{dest.stat().st_size/1e6:.1f} MB")
                log(f"{ds_id}: OK {dest.stat().st_size/1e6:.1f} MB")
                break
            last_err = r["error"]
            log(f"{ds_id}: FAILED - {last_err}")
        else:
            entry.update(status="BLOCKED", detail=last_err + f"  -> download by hand and place in data/manual/{ds_id}/")
            record_source(dict(source_id=f"{ds_id}::unavailable", dataset_id=ds_id, title=ds["title"], organisation=ds["organisation"],
                               authority=ds.get("authority"), landing_page=ds.get("landing_page"), url=ds["urls"][0]["url"],
                               url_status=f"{ds['urls'][0]['status']} -> FAILED", retrieved_at=utc_iso(), kind="STATIC",
                               status="BLOCKED", note=last_err, coverage=ds.get("coverage"), licence=ds.get("licence"),
                               limitations=ds.get("limitations")))
    report["finished_at"] = utc_iso()
    return report
