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

`web/data/` is **empty on purpose**. The site shows "No data has been built yet" until you run the pipeline. Nothing is
pre-filled, so nothing can be mistaken for real data.

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
| CWC monthly glacial-lake PDFs | Manual (PDF); the interface says "No current data" for current lake change |
| CWC flood portals (FFS/AFF), India-WRIS WMS | Not used: robots/auth blocked or unreachable in your test |
| Census 2011 villages, official Indian basin boundaries (25 basins / 101 sub-basins) | No verified download; not used |

## Evidence labels (shown on every card)

**Official** (issued by a government agency) - **Current** (within the freshness window) - **Latest official update**
(newest official version, not live) - **Computed spatial connection** (calculated from map geometry) - **Scenario**
(a user-chosen setting such as the display horizon) - **No current data**.

## Honest limits

* River links use HydroRIVERS (about 500 m source resolution, modelled discharge). Small Himalayan streams are missing.
* Exposure is *what lies near the traced river* (2 km corridor), not damage. Missing datasets are shown as No data, never as zero.
* State/district outlines come from the NWIC portal and are not confirmed as the legal external boundary of India.
* Not yet tested against the real government servers: the first run in Colab is the real test. Read `web/data/build_status.json`
  (also shown in the Sources tab) for every blocker and the exact next action.

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

## Secrets (never paste keys into chat or files)

Settings > Secrets and variables > Actions > New repository secret: `IMD_API_KEY`.
