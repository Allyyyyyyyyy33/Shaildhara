"""Run the whole SHAILDHARA data pipeline.

  python -m pipeline.build_all                # everything except the heavy downloads
  python -m pipeline.build_all --heavy        # also try WorldPop and the OpenStreetMap extract (large)
  python -m pipeline.build_all --live         # only refresh the live sources (needs the static build done once)
  python -m pipeline.build_all --stages static,foundation,connectivity

Each stage runs in isolation: a failure is recorded as a blocker and the next stage still runs.
"""
import argparse
import traceback

from . import build_connectivity, build_exposure, build_foundation, fetch_live, fetch_static
from .common import WEB_DATA, ensure_dirs, log, publish_sources, save_json, short, utc_iso

STAGES = ["static", "foundation", "connectivity", "exposure", "live"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="", help="comma-separated subset of: " + ",".join(STAGES))
    ap.add_argument("--heavy", action="store_true", help="include large downloads (WorldPop, OpenStreetMap)")
    ap.add_argument("--live", action="store_true", help="shortcut for --stages live")
    ap.add_argument("--force", action="store_true", help="download static files again")
    a = ap.parse_args(argv)
    chosen = ["live"] if a.live else ([s for s in a.stages.split(",") if s] or STAGES)
    ensure_dirs()
    report = {"generated_at": utc_iso(), "stages": {}, "blockers": [], "actions": []}
    old = None
    try:
        import json
        old = json.load(open(WEB_DATA / "build_status.json"))
        report["stages"] = old.get("stages", {})
    except Exception:
        pass
    runners = {
        "static": lambda: fetch_static.run(heavy=a.heavy, force=a.force),
        "foundation": build_foundation.run,
        "connectivity": build_connectivity.run,
        "exposure": build_exposure.run,
        "live": fetch_live.run,
    }
    for st in STAGES:
        if st not in chosen:
            continue
        log(f"=== stage: {st} ===")
        try:
            report["stages"][st] = runners[st]()
        except Exception as e:
            log(f"stage {st} CRASHED: {short(e)}")
            traceback.print_exc(limit=3)
            report["stages"][st] = {"status": "CRASHED", "error": short(e)}
    # collect blockers + the exact actions needed from the owner
    s = report["stages"]
    for ds_id, d in (s.get("static", {}).get("datasets") or {}).items():
        if d["status"] in ("BLOCKED", "BLOCKED_MANUAL"):
            report["blockers"].append(f"Static data '{ds_id}': {d['detail']}")
    for stg in ("foundation", "connectivity", "exposure"):
        for b in (s.get(stg, {}).get("blockers") or []):
            report["blockers"].append(f"{stg}: {b}")
        for u in (s.get(stg, {}).get("unavailable") or []):
            report["blockers"].append(f"exposure layer '{u['key']}': {u['reason']}")
    live = s.get("live", {}).get("sources", {}) if isinstance(s.get("live"), dict) else {}
    for sid, e in live.items():
        if e.get("status") in ("BLOCKED", "NEEDS_API_KEY", "ERROR", "CATALOGUE_ONLY"):
            report["blockers"].append(f"Live source '{sid}': {e.get('detail')}")
        for b in (e.get("blockers") or [])[:3]:
            report["blockers"].append(f"Live source '{sid}': {b}")
    if live.get("imd", {}).get("status") == "NEEDS_API_KEY":
        report["actions"].append("Set your IMD_API_KEY (environment variable / Colab secret / GitHub secret) and re-run with --live.")
    if any("hydrorivers" in b.lower() for b in report["blockers"]):
        report["actions"].append("Download HydroRIVERS (Asia) from hydrosheds.org and place the ZIP in data/manual/hydrorivers_asia/, then re-run.")
    save_json(WEB_DATA / "build_status.json", report, compact=False)
    publish_sources()
    log(f"Build finished. {len(report['blockers'])} blocker note(s). See web/data/build_status.json")
    return report


if __name__ == "__main__":
    main()
