# SHAILDHARA - From the Peak to the People

An India-wide, mountain-to-downstream environmental evidence platform. It connects current official signals
(alerts, warnings, observations) to the river network, shows what lies along the connected path, and labels every
statement with how it is known.

**It does not predict disasters.** Official forecasts and warnings stay official and are shown as issued. The chain is a
*computed spatial connection* (a geographic link from map data), never a physical forecast, a causal claim or a loss estimate.

## What is in this folder

| Folder | What it is |
|---|---|
| `pipeline/` | Python data pipeline: download, standardise, audit, build the river graph, compute exposure, fetch live feeds |
| `web/` | The website (static files: HTML, JS, CSS). Reads only what the pipeline wrote into `web/data/` |
| `config/` | `sources.json` (dataset registry) and `live_sources.json` (feed settings, no secrets) |
| `data/manual/` | Drop-in folders for files that cannot be downloaded automatically |
| `tests/` | Developer tests. They use throwaway fictional fixtures in a temporary folder and never touch `web/data/` |

`web/data/` holds the committed build produced by the pipeline (boundaries, glacial lakes, river network, exposure, live status). Nothing
in it is hand-written: if a file is missing the site says so instead of filling the gap. The static layers are snapshots; only
`web/data/live/` is refreshed on a schedule.

## Option A (fastest, recommended): Colab notebook + Netlify Drop

1. Open `SHAILDHARA_build.ipynb` in Google Colab (File > Upload notebook).
2. Run the cells top to bottom; upload `shaildhara_project.zip` when asked. The notebook builds the data, prints every blocker,
   runs the deploy check and downloads `web.zip`.
3. Unzip `web.zip`, test with `python -m http.server` inside `web/` (http://localhost:8000), then drag the `web/` folder onto
   https://app.netlify.com/drop for a public link. `netlify.toml` is included if you connect a repository instead.
4. Put working SACHET feed URLs from your endpoint test into `config/live_sources.json` (`sachet.feeds`) for reliable alerts.

## Option B: GitHub (scheduled live refresh + free hosting on GitHub Pages)

Create a GitHub repository, upload this folder, then: *Actions > Build SHAILDHARA data (manual) > Run workflow*.
`build-data.yml` (manual) builds everything and commits `web/data`; `refresh-live.yml` refreshes live sources every 3 hours; `pages.yml` publishes `web/` to GitHub Pages (Settings > Pages > Source: GitHub Actions). **Caution:** some Indian government sites may refuse
GitHub's servers. If the Sources tab shows BLOCKED, use Option A. Add the IMD key under
*Settings > Secrets and variables > Actions > New secret* named `IMD_API_KEY`.

## What is automated, what is manual, what is blocked

| Item | Status |
|---|---|
| State/district boundaries, river lines, glacial lakes (NWDP/CWC) | Automatic. URLs come from the portal's own pages; the first real download is your run |
| River graph (downstream links) | Automatic **if** HydroRIVERS downloads. Its URL is an unverified candidate; if it fails, download HydroRIVERS (Asia) from hydrosheds.org and put the ZIP in `data/manual/hydrorivers_asia/` |
| SACHET alerts | Automatic once feed URLs are known; expired alerts are dropped; district matching by polygon or by name (labelled) |
| NWDP/CWC telemetry | Catalogue always; station observations only when a dataset exposes recognisable station, coordinates, time and value fields |
| IMD API | **Blocked until you add `IMD_API_KEY`.** IMD also describes IP whitelisting: if calls return 401/403 with a valid key, ask IMD's nodal officer to whitelist the machine |
| Population (WorldPop), OpenStreetMap | Heavy; run with `--heavy`. Candidate URLs, unverified. Or place files in `data/manual/...` |
| Land cover, protected areas/wetlands | Manual files in `data/manual/landcover/` and `data/manual/ecosystems/` |
| CWC monthly glacial-lake PDFs | Manual (PDF); the interface says current lake change is "not published as data" |
| CWC flood portals (FFS/AFF), India-WRIS WMS | Not used: robots/auth blocked or unreachable in your test |
| Census 2011 villages, official Indian basin boundaries (25 basins / 101 sub-basins) | No verified download; not used |

## Evidence labels (shown on every card)

**Official** (issued by a government agency) - **Current** (within the freshness window) - **Latest official update**
(newest official version, not live) - **Computed spatial connection** (calculated from map geometry) - **Nearest mapped river
(unverified)** (a lake matched to the closest river only; drainage not confirmed) - **Scenario** (a user-chosen setting such as the
display horizon) - **Not available** (no reliable machine-readable information exists; shown as blank, never estimated).

## What SHOW ME THE CHAIN does and does not claim

* **Alert or district start:** the chain takes mapped river reaches that lie inside the district(s). That is geographic overlap, not a
  statement about what caused the alert.
* **Glacial-lake start:** the lake is joined to the *nearest* mapped river reach. Nothing in the CWC inventory says where a lake drains,
  so this is always a proximity candidate, labelled "Drainage connection unverified" and drawn with a dashed line. Links of 5 km or
  more are **not traced at all** (the interface explains why); lakes with no reach within 10 km show no connection.
* **Downstream path:** follows the drawing direction of OpenStreetMap waterways. It is not a hydrological model, not an official river
  map and not a flow, arrival-time or inundation forecast. Where a river forks, a fixed rule picks one branch; the count of such forks
  is shown in the interface. Gaps or mis-drawn waterways can break a path.
* **What an event can lead to, and its consequences:** taken from `web/data/hazard_guidance.json`, a hand-curated file where every entry
  cites an official document (IMD National Bulletin "Impact Expected" sections for heavy rain and cyclones; NDMA's GLOF guidelines for
  glacial lakes). It is general guidance for that type of event, shown with its source link, never a forecast for a place. To add a type
  of event, add an entry with an institution, title and https link; entries without a source link are ignored.
* **Exposure:** what lies within a 2 km corridor of the traced reaches. Not damage, not risk, not people affected.

## Honest limits

* River links use HydroRIVERS (about 500 m source resolution, modelled discharge). Small Himalayan streams are missing.
* Exposure is *what lies near the traced river* (2 km corridor), not damage. Missing datasets are shown as No data, never as zero.
* State/district outlines come from the NWIC portal and are not confirmed as the legal external boundary of India.
* Live government feeds are exercised by the scheduled GitHub workflow (Actions > Refresh live data), not by the developer tests. Read
  `web/data/build_status.json` (also shown under Data & Sources) for every blocker and the exact next action.
* Static layers (boundaries, lakes, river lines, exposure) are dated snapshots. The interface shows which rows are a live feed and
  which are snapshots, with their data dates and last-checked times.
* Station readings come from the NWDP/CWC datasets that are machine-readable; coverage is partial, and the newest reading can be
  days old. The interface labels them "latest official update", never "live".

## Deploy check

`python -m pipeline.check_deploy` states what the public site will show and refuses (exit 1) if data is missing or if the fictional test fixtures are present in `web/data`.

## Developer tests

```
python tests/test_pipeline.py                      # pipeline against fictional fixtures in a temp folder
node tests/test_chain.js <web/data folder built by that test>
```


## Files that cannot be downloaded automatically (e.g. HydroRIVERS)

HydroSHEDS refuses automated downloads (HTTP 403), and automated access is not worked around. Download the file in a browser, then on GitHub:
Releases > Draft a new release > tag `data-v1` > attach the file (HydroRIVERS_v10_as_shp.zip, ind_ppp_*.tif, *.osm.pbf, ... - routed by file name,
see `tools/stage_release_files.py`) > Publish release. The next build picks it up automatically.

## What the scheduled refresh changes

`refresh-live.yml` (every 3 hours) commits only `web/data/live/`, `web/data/build_status.json`, `web/data/sources.json` and
`web/data/coverage.json`. It never edits website code or the static layers. Its own time budget is 18 minutes (`live_total_seconds` in
`config/live_sources.json`) with a 22-minute step limit and 30-minute job limit; JSON files are written atomically and checked before commit.

## Secrets (never paste keys into chat or files)

Settings > Secrets and variables > Actions > New repository secret: `IMD_API_KEY`.
