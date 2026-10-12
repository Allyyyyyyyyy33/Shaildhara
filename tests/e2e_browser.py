"""Browser end-to-end tests of the built site, run on the REAL committed data (web/data), in Chromium.
   python tests/e2e_browser.py [LEAFLET_DIST_DIR]        # local server; leaflet dir only needed when the CDN is unreachable (offline sandbox)
   python tests/e2e_browser.py --url https://allyyyyyyyyy33.github.io/Shaildhara/   # same checks against a deployed site (needs internet)
Simulated: the clock (to replay the moment the data was fresh), unreachable/malformed data files. Real: all site code and data.
Not covered here: real government servers (those are exercised by the scheduled GitHub run)."""
import sys, threading, http.server, socketserver, functools, json, pathlib, datetime
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1] / "web"
args = sys.argv[1:]
url = None
if "--url" in args:
    url = args[args.index("--url") + 1]; args = [a for a in args if a not in ("--url", url)]
leaf = pathlib.Path(args[0]) if args else None
fails = []


def check(c, m):
    print(("ok  : " if c else "FAIL: ") + m)
    if not c:
        fails.append(m)


if not url:
    H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    H.log_message = lambda *a, **k: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
gen = json.load(open(ROOT / "data" / "live" / "alerts.json"))["generated_at"]
DATA_TIME = datetime.datetime.fromisoformat(gen) + datetime.timedelta(minutes=5)     # replay "just after the feed was read"
ALL_ERRS = []


def ink(pg, x0=0, x1=None):
    return pg.evaluate("""([x0, x1]) => Array.from(document.querySelectorAll('#map canvas')).reduce((n, c) => {
        try { const w = Math.min(c.width, x1 == null ? c.width : x1); const d = c.getContext('2d').getImageData(x0, 0, Math.max(1, w - x0), c.height).data;
              for (let i = 3; i < d.length; i += 4) if (d[i]) n++; } catch (e) {} return n; }, 0)""", [x0, x1])


def page(P, w, h, fixed_time=None, setup=None, wait_net=True):
    b = P.chromium.launch(); pg = b.new_page(viewport={"width": w, "height": h}); errs = []
    pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
    if fixed_time: pg.clock.set_fixed_time(fixed_time)
    if setup: setup(pg)
    if leaf:
        pg.route("**/leaflet.min.js", lambda r: r.fulfill(path=str(leaf / "leaflet.js"), content_type="application/javascript"))
        pg.route("**/leaflet.min.css", lambda r: r.fulfill(path=str(leaf / "leaflet.css"), content_type="text/css"))
    pg.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(body=PNG, content_type="image/png"))
    pg.goto(url, wait_until="load")
    pg.wait_for_function("window.SHAILDHARA && window.SHAILDHARA.D" + (" && window.SHAILDHARA.D.net" if wait_net else ""), timeout=60000)
    pg.wait_for_timeout(1200)
    return b, pg, errs


def noise(errs):
    return [e for e in errs if "net::ERR" not in e and "favicon" not in e.lower() and "Failed to load resource" not in e]


with sync_playwright() as P:
    # ---------- 1. desktop, at the moment the data was fresh
    b, pg, errs = page(P, 1440, 900, DATA_TIME)
    print("     status bar:", pg.inner_text("#top-status").replace("\n", " "))
    check(pg.locator("meta[name=shaildhara-build]").count() == 1 and "Build 2026" in pg.inner_text("footer"), "build stamp present in the page")
    check(ink(pg) > 5000, f"district/state/lake outlines drawn on the map ({ink(pg)} px)")
    check(pg.locator("#loading").is_hidden(), "loading overlay is dismissed after startup")
    check(pg.locator(".mapkey").count() == 1, "map key is present")
    n = pg.locator(".card[data-alert]").count(); check(n > 0, f"{n} alert cards shown when alerts are unexpired")
    txt = pg.locator(".card[data-alert]").first.inner_text()
    check("IST" in txt and "issued" in txt and "Source:" in txt, "alert card shows issue time (IST), expiry and provenance")
    btn = pg.locator("button[data-chain-alert]:not([disabled])").first
    ink0 = ink(pg); btn.click(); pg.wait_for_selector("#chainpanel:not([hidden])", timeout=20000); pg.wait_for_timeout(3000)
    panel = pg.inner_text("#chainpanel"); left = pg.evaluate("document.querySelector('#chainpanel').getBoundingClientRect().left")
    check("Computed spatial connection" in panel and "Original alert (CAP)" in panel, "chain labelled computed and links to the original alert")
    check("sachet.ndma.gov.in" in (pg.locator("#chainpanel a[href*='sachet']").first.get_attribute("href") or ""), "chain links to the official SACHET source")
    check("predict" in pg.inner_text("footer").lower(), "no-prediction disclaimer is visible")
    left_ink = ink(pg, 0, int(left) - 40)
    check(ink(pg) > ink0 + 200 and left_ink > 3000, f"traced chain is drawn in the map area NOT hidden behind the panel (panel starts at x={int(left)}px)")
    pg.click("button[data-tab=search]"); pg.fill("#q", "Dehradun"); pg.wait_for_timeout(500)
    check("Dehra" in pg.inner_text("#results"), "search finds a district"); pg.fill("#q", "Uttarakhand"); pg.wait_for_timeout(500)
    check("Uttarakhand" in pg.inner_text("#results"), "search finds a state")
    lake = pg.evaluate("() => { const f = window.SHAILDHARA.D.lakes.features.find(f => f.properties.name && f.properties.reach != null); return f ? f.properties.name : null }")
    pg.fill("#q", lake or "x"); pg.wait_for_timeout(500); check(bool(lake) and lake.lower() in pg.inner_text("#results").lower(), f"search finds a glacial lake ({lake})")
    pg.fill("#q", "Flood"); pg.wait_for_timeout(500); check("lood" in pg.inner_text("#results"), "search finds alerts")
    pg.click("button[data-tab=layers]"); check(len(pg.inner_text("#tab-layers")) > 200, "Layers tab renders")
    boxes = pg.locator("#tab-layers input[type=checkbox]"); check(boxes.count() >= 3, f"Layers has {boxes.count()} toggles")
    base = ink(pg); changed = 0
    for i in range(boxes.count()):
        boxes.nth(i).click(); pg.wait_for_timeout(300); changed += ink(pg) != base; boxes.nth(i).click(); pg.wait_for_timeout(200)
    check(changed >= 2, f"Layers toggles change what the map draws ({changed} of {boxes.count()} redraw the canvas; the rest are basemap/labels)")
    pg.click("button[data-tab=sources]"); src = pg.inner_text("#tab-sources")
    check(len(src) > 500 and ("BLOCKED" in src.upper() or "NEEDS" in src.upper()), "Sources lists feeds and states blockers honestly")
    check(pg.locator("#tab-sources a[href^=http]").count() >= 3, "Sources has official source links")
    pg.click("button[data-tab=coverage]"); cov = pg.inner_text("#tab-coverage")
    check("28/28" in cov and "8/8" in cov and "Blocked or unavailable" in cov and "Available but incomplete" in cov, "Coverage tab shows measured counts and the honest category split")
    pg.keyboard.press("Tab"); check(pg.evaluate("document.activeElement && document.activeElement !== document.body"), "keyboard focus moves between controls")
    ALL_ERRS += errs; b.close()

    # ---------- 2. desktop, REAL current time: nothing expired may be shown as current
    b, pg, errs = page(P, 1440, 900)
    ok = pg.evaluate("() => (window.SHAILDHARA.D.alerts.alerts || []).every(a => !a.expires || Date.parse(a.expires) >= Date.now())")
    check(ok, "at the real current time every alert shown is within its own validity period")
    print("     signals head:", pg.inner_text("#tab-signals").replace("\n", " | ")[:200])
    ALL_ERRS += errs; b.close()

    # ---------- 3. the alert feed was unreachable in the latest refresh: say so
    def _down(route):
        r = route.fetch(); d = r.json(); d["sources"]["sachet"]["status"] = "BLOCKED"; route.fulfill(json=d)
    b, pg, errs = page(P, 1440, 900, DATA_TIME, setup=lambda p: p.route("**/live/status.json", _down))
    check("could not be read in the latest refresh" in pg.inner_text("#tab-signals"), "unreachable feed: warning shown above the alerts that are kept")
    ALL_ERRS += errs; b.close()

    # ---------- 4. mobile layout
    b, pg, errs = page(P, 390, 844, DATA_TIME)
    r = pg.evaluate("""() => { const q = s => document.querySelector(s).getBoundingClientRect();
        return { left: q('#left'), foot: q('footer'), map: q('#mapwrap'), sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }; }""")
    check(r["foot"]["top"] >= r["left"]["bottom"] - 1, "mobile: footer sits below the panels, it does not overlap the alert list")
    check(r["sw"] <= r["cw"] + 1, "mobile: no sideways scrolling")
    check(r["map"]["height"] >= 250, f"mobile: map is usable ({int(r['map']['height'])}px high)")
    pg.locator("button[data-chain-alert]:not([disabled])").first.click(); pg.wait_for_selector("#chainpanel:not([hidden])", timeout=20000); pg.wait_for_timeout(3000)
    r = pg.evaluate("""() => { const q = s => document.querySelector(s).getBoundingClientRect(); return { panel: q('#chainpanel'), map: q('#mapwrap') }; }""")
    check(r["panel"]["top"] >= r["map"]["bottom"] - 2 or pg.evaluate("window.scrollY") > 0, "mobile: chain panel does not cover the map")
    check(pg.locator("#chainpanel").is_visible(), "mobile: chain panel is reachable")
    ALL_ERRS += errs; b.close()

    # ---------- 5. degraded data: a broken or malformed layer must not blank the application
    def _break(path, body, status=200):
        return lambda p: p.route("**/" + path, lambda r: r.fulfill(status=status, body=body, content_type="application/json"))
    cases = {
        "glacial-lake layer unreachable (HTTP 500)": _break("data/glacial_lakes.geojson", "oops", 500),
        "alerts file malformed": _break("data/live/alerts.json", '{"alerts": "not-a-list"}'),
        "alerts file empty object": _break("data/live/alerts.json", "{}"),
        "observations contain NaN/null coordinates": _break("data/live/river_observations.json",
            json.dumps({"observations": [{"station": "X", "lat": None, "lon": None, "value": None, "parameter": "p", "observed_at": "2026-01-01T00:00:00Z"}]})),
        "river network file missing": _break("data/network/reaches.json", "", 404),
        "coverage file missing": _break("data/coverage.json", "", 404),
    }
    for name, setup in cases.items():
        b, pg, errs = page(P, 1280, 800, DATA_TIME, setup=setup, wait_net=False)
        up = pg.evaluate("!!window.SHAILDHARA") and pg.locator(".leaflet-container").count() == 1 and len(pg.inner_text("#tab-signals")) > 20
        check(up, f"degraded: {name} -> site still starts and Signals renders")
        pg.click("button[data-tab=coverage]"); check(len(pg.inner_text("#tab-coverage")) > 20, f"degraded: {name} -> Coverage tab still renders")
        ALL_ERRS += [e for e in errs if "pageerror" in e]; b.close()

errs = noise(ALL_ERRS)
check(not errs, "no uncaught JavaScript errors" + ("" if not errs else ": " + "; ".join(errs[:4])))
print("\n%d check(s) failed" % len(fails) if fails else "\nBROWSER E2E PASSED")
sys.exit(1 if fails else 0)
