"""Deploy readiness check - run after the build, before publishing:  python -m pipeline.check_deploy

Reads web/data/ and tells you in plain words what the public site will show, what is missing, and
whether it is safe to publish. It also refuses to pass if test fixtures (fictional data) are present.
Exit code: 0 = publishable, 1 = not publishable.
"""
import sys

from .common import WEB, WEB_DATA, WEB_LIVE, read_json

FIXTURE_MARKERS = ("Teststate", "Testganga", "Closelake", "Midlake", "Farlake")


def _size(p):
    return p.stat().st_size / 1e6 if p.exists() else 0.0


def main():
    problems, warnings, info = [], [], []
    need = {"states.geojson": "state outlines", "districts.geojson": "district outlines", "glacial_lakes.geojson": "glacial lakes",
            "search_index.json": "search", "sources.json": "source list", "build_status.json": "build report"}
    for f, what in need.items():
        if not (WEB_DATA / f).exists():
            problems.append(f"missing {f} ({what}) - run the pipeline stages static + foundation")
    # fixture guard
    for f in ("states.geojson", "search_index.json", "glacial_lakes.geojson"):
        p = WEB_DATA / f
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if any(m in txt for m in FIXTURE_MARKERS):
                problems.append(f"{f} contains FICTIONAL TEST DATA - delete web/data and rebuild from real sources")
    # coordinate sanity: web layers must be lon/lat degrees that fall inside India's neighbourhood
    for f in ("states.geojson", "districts.geojson", "glacial_lakes.geojson"):
        g = read_json(WEB_DATA / f)
        if not g or not g.get("features"):
            continue
        def first(c):
            while isinstance(c, list) and c and isinstance(c[0], list):
                c = c[0]
            return c
        bad = 0
        for ft in g["features"][:300]:
            x, y = first(ft["geometry"]["coordinates"])[:2]
            if not (60 <= x <= 105 and 0 <= y <= 42):
                bad += 1
        if bad:
            problems.append(f"{f}: {bad} of the first {min(300, len(g['features']))} features are outside India's lon/lat range - a projection problem; rebuild after the CRS fix")
    net = read_json(WEB_DATA / "network" / "reaches.json")
    anchors = read_json(WEB_DATA / "anchors.json")
    chain_ok = bool(net and anchors)
    if chain_ok:
        info.append(f"River graph: {len(net['ids']):,} reaches ({_size(WEB_DATA / 'network' / 'reaches.json'):.1f} MB). SHOW ME THE CHAIN works.")
    else:
        warnings.append("No river graph (HydroRIVERS missing/blocked): the map shows layers but SHOW ME THE CHAIN is disabled. "
                        "Put HydroRIVERS_v10_as_shp.zip in data/manual/hydrorivers_asia/ and re-run.")
    expo = read_json(WEB_DATA / "exposure" / "reach_exposure.json")
    if expo and expo.get("layers"):
        info.append("Exposure layers: " + ", ".join(sorted(expo["layers"])))
        for u in expo.get("unavailable", []):
            warnings.append(f"exposure '{u['key']}' unavailable: {u['reason']}")
    elif chain_ok:
        warnings.append("No exposure layers built: step 5 of the chain will say 'No current data' (honest, but thin). Run with --heavy or add manual files.")
    st = read_json(WEB_LIVE / "status.json")
    if st:
        for sid, e in st.get("sources", {}).items():
            line = f"live {sid}: {e.get('status')} ({e.get('records', 0)} records) - {e.get('detail', '')[:140]}"
            (info if e.get("status") in ("OK", "NO_CURRENT_ALERTS") else warnings).append(line)
    else:
        warnings.append("Live stage has not run: site will show 'No current data' for alerts. Run with --live.")
    big = [(p.name, _size(p)) for p in WEB_DATA.rglob("*.json*") if _size(p) > 25]
    for n, s in big:
        warnings.append(f"{n} is {s:.0f} MB: slow first load; consider a higher simplification tolerance")
    total = sum(p.stat().st_size for p in WEB.rglob("*") if p.is_file()) / 1e6
    info.append(f"Total site size: {total:.1f} MB")
    print("=== SHAILDHARA deploy check ===")
    for i in info:
        print("  ok   ", i)
    for w in warnings:
        print("  note ", w)
    for p in problems:
        print("  BLOCK", p)
    verdict = "NOT READY TO PUBLISH" if problems else ("READY - with the limits listed above" if warnings else "READY")
    print("VERDICT:", verdict)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
