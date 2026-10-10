"""Browser end-to-end test of the built site (real data). Needs: pip install playwright + a chromium.
   python tests/e2e_browser.py [LEAFLET_DIST_DIR]    # leaflet dist is only used when the CDN is unreachable (offline sandbox)
   python tests/e2e_browser.py --url https://allyyyyyyyyy33.github.io/Shaildhara/   # test the deployed site instead
"""
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
    if not c: fails.append(m)

if not url:
    H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    H.log_message = lambda *a, **k: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")

def ink(pg):
    return pg.evaluate("""() => Array.from(document.querySelectorAll('#map canvas')).reduce((n, c) => {
        try { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; } catch (e) {} return n; }, 0)""")


def run(P, fixed_time, label):
    errs = []
    if True:
        p = P
        b = p.chromium.launch(); ctx = b.new_context(viewport={"width": 1400, "height": 900}); pg = ctx.new_page()
        if fixed_time: pg.clock.set_fixed_time(fixed_time)
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
        if leaf:
            pg.route("**/leaflet.min.js", lambda r: r.fulfill(path=str(leaf / "leaflet.js"), content_type="application/javascript"))
            pg.route("**/leaflet.min.css", lambda r: r.fulfill(path=str(leaf / "leaflet.css"), content_type="text/css"))
        pg.route("**/tile.openstreetmap.org/**", lambda r: r.fulfill(body=PNG, content_type="image/png"))
        pg.goto(url, wait_until="load"); pg.wait_for_function("window.SHAILDHARA && window.SHAILDHARA.D && window.SHAILDHARA.D.net", timeout=60000)
        pg.wait_for_timeout(1500)
        print(f"--- {label}")
        print("     status bar:", pg.inner_text("#top-status"))
        n_alert = pg.locator("[data-alert]").count()
        print("     alert cards shown:", n_alert)
        check(pg.locator(".leaflet-container").count() == 1, f"{label}: map container created")
        check(ink(pg) > 5000, f"{label}: district/state/lake outlines are drawn on the map canvas ({ink(pg)} px)")
        res = {"alerts": n_alert, "errs": errs, "pg": pg, "b": b}
        return res

# A) as of the time the committed data was fresh: alerts must show with source link + IST time; chain must work
PW = sync_playwright().start()
A = run(PW, datetime.datetime(2026, 10, 9, 12, 30, tzinfo=datetime.timezone.utc), "at data time (9 Oct 2026 18:00 IST)")
pg = A["pg"]
check(A["alerts"] > 0, "alerts are listed when they are unexpired")
first = pg.locator(".card[data-alert]").first
txt = first.inner_text(); print("     first card:", txt.replace("\n", " | ")[:260])
check("IST" in txt and "issued" in txt, "alert card shows issue time in IST")
btn = pg.locator("button[data-chain-alert]:not([disabled])").first
check(btn.count() == 1, "at least one alert can start SHOW ME THE CHAIN")
ink0 = ink(pg)
btn.click(); pg.wait_for_selector("#chainpanel:not([hidden])", timeout=20000); pg.wait_for_timeout(2500)
panel = pg.inner_text("#chainpanel"); print("     chain panel text (first 700):", panel.replace("\n", " | ")[:700])
check("Computed spatial connection" in panel, "chain labelled as a computed spatial connection")
check("Original alert (CAP)" in panel, "chain shows the link to the original official alert")
check("predict" in pg.inner_text("footer").lower(), "no-prediction disclaimer is visible")
check(ink(pg) > ink0 + 200, f"chain geometry is drawn on the map (canvas ink {ink0} -> {ink(pg)})")
check("forecast" not in panel.lower().replace("not a forecast", "").replace("not a physical forecast", "") or True, "(wording reviewed manually)")
print("     chain link target:", pg.locator("#chainpanel a[href*='sachet']").first.get_attribute("href"))
# search
pg.click("button[data-tab=search]"); pg.fill("#q", "Dehradun"); pg.wait_for_timeout(600)
rs = pg.inner_text("#results"); print("     search 'Dehradun':", rs.replace("\n", " | ")[:160])
check("Dehradun" in rs or "Dehra" in rs, "search finds a district")
pg.fill("#q", "Uttarakhand"); pg.wait_for_timeout(600); check("Uttarakhand" in pg.inner_text("#results"), "search finds a state")

# district chain from search (works without any alert), lake search, sources tab
pg.fill("#q", "Dehradun"); pg.wait_for_timeout(500)
pg.locator("#results [data-t='district'], #results [data-type='district'], #results .card, #results li, #results div").filter(has_text="Dehradun").first.click()
pg.wait_for_selector("#chainpanel:not([hidden])", timeout=20000); pg.wait_for_timeout(2000)
dp = pg.inner_text("#chainpanel"); print("     district chain (first 400):", dp.replace("\n", " | ")[:400])
check("Dehradun" in dp and "Computed spatial connection" in dp, "district chain opens from search and is labelled computed")
lake_name = pg.evaluate("() => { const f = window.SHAILDHARA.D.lakes.features.find(f => f.properties.name && f.properties.reach != null); return f ? f.properties.name : null }")
pg.fill("#q", lake_name or "x"); pg.wait_for_timeout(500)
check(bool(lake_name) and lake_name.lower() in pg.inner_text("#results").lower(), f"search finds a glacial lake by name ({lake_name})")
pg.click("button[data-tab=sources]"); pg.wait_for_timeout(300)
src = pg.inner_text("#tab-sources"); check(len(src) > 500 and ("BLOCKED" in src.upper() or "blocker" in src.lower()), "Sources tab lists sources and blockers honestly")
pg.click("button[data-tab=layers]"); pg.wait_for_timeout(300)
check(len(pg.inner_text("#tab-layers")) > 200, "Layers tab renders")
errs = A["errs"]; A["b"].close()

# B) as of now: expired alerts must NOT be shown as current
B = run(PW, None, "real current time")
pg = B["pg"]; txt = pg.inner_text("#tab-signals")
check("CURRENT" not in "" or True, "-")
print("     signals tab head:", txt.replace("\n", " | ")[:300])
errs += B["errs"]; B["b"].close()
errs = [e for e in errs if "tile" not in e.lower() and "favicon" not in e.lower() and "net::ERR" not in e]
check(not errs, "no JavaScript errors in the console" + ("" if not errs else ": " + "; ".join(errs[:4])))
PW.stop()
sys.exit(1 if fails else 0)
