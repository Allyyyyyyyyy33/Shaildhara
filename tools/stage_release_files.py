"""Move files you attached to the GitHub release 'data-v1' into the manual drop-in folders.

    python tools/stage_release_files.py <downloaded-dir> [data/manual]

A file is routed by name:
  HydroRIVERS*.zip            -> hydrorivers_asia
  HydroBASINS*.zip            -> hydrobasins_asia
  ind_ppp*.tif                -> worldpop_india
  *.osm.pbf                   -> osm_india
  *WorldCover*.tif            -> landcover
  <dataset_id>__<anything>    -> that dataset folder (explicit prefix)
Anything else is reported and left alone - nothing is guessed.
"""
import re
import shutil
import sys
from pathlib import Path

RULES = [(r"^hydrorivers.*\.zip$", "hydrorivers_asia"), (r"^hydrobasins.*\.zip$", "hydrobasins_asia"), (r"^ind_ppp.*\.tif{1,2}$", "worldpop_india"),
         (r".*\.osm\.pbf$", "osm_india"), (r".*worldcover.*\.tif{1,2}$", "landcover")]
KNOWN = {"hydrorivers_asia", "hydrobasins_asia", "worldpop_india", "osm_india", "landcover", "ecosystems"}


def route(name):
    if "__" in name:
        ds, rest = name.split("__", 1)
        if ds in KNOWN and rest:
            return ds, rest
    for pat, ds in RULES:
        if re.match(pat, name, re.I):
            return ds, name
    return None, name


def main(src, dest="data/manual"):
    src, dest = Path(src), Path(dest)
    moved = []
    for f in sorted(src.glob("*")):
        if not f.is_file():
            continue
        ds, name = route(f.name)
        if ds is None:
            print(f"skipped (not recognised): {f.name}")
            continue
        (dest / ds).mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dest / ds / name))
        print(f"staged {f.name} -> {dest / ds / name}")
        moved.append(ds)
    return moved


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(*sys.argv[1:3])
