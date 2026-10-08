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

## Option A (recommended first): run it in Google Colab - one cell

Your endpoint test already ran from Colab, so this route is known to reach the sources.

1. Zip this folder (or upload the zip you were given) to Colab: Files panel > Upload.
2. In a new notebook, paste into ONE cell and run:

```python
!unzip -q -o shaildhara_project.zip -d /content && cd /content/shaildhara && pip -q install -r requirements.txt
import os
os.environ["SHAILDHARA_CONTACT"] = ""            # optional: your email
# os.environ["IMD_API_KEY"] = "..."              # only after IMD issues you a key; prefer Colab Secrets
%cd /content/shaildhara
!python -m pipeline.build_all
```
3. Put working SACHET feed URLs from your test into `config/live_sources.json` (`sachet.feeds`) and run again with
   `!python -m pipeline.build_all --live`.
4. Download the `web/` folder (`!zip -r web.zip web`), unzip it on your computer, and test it with
   `python -m http.server` inside the folder, then open http://localhost:8000.
5. Publish by dragging the `web/` folder onto Netlify Drop (or any static host).

## Option B: GitHub (one click, scheduled live refresh)

Create a GitHub repository, upload this folder, then: *Actions > Build SHAILDHARA data (manual) > Run workflow*.
The workflow `refresh-live.yml` refreshes live sources every 3 hours. **Caution:** some Indian government sites may refuse
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

## Developer tests

```
python tests/test_pipeline.py                      # pipeline against fictional fixtures in a temp folder
node tests/test_chain.js <web/data folder built by that test>
```
