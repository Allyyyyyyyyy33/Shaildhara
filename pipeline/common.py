"""Shared helpers for the SHAILDHARA pipeline.

Rules enforced here for every network request:
  * robots.txt is checked first; disallowed paths are skipped, never fetched
  * no login/credential bypass, no retries after 401/403/429, SSL verification always on
  * a pause between requests, and an honest User-Agent
Every dataset or feed that is fetched gets a provenance record (source URL, retrieval time,
SHA-256, data date/version, coverage, limitations).
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

ROOT = Path(os.environ.get("SHAILDHARA_ROOT", Path(__file__).resolve().parents[1]))
DATA = ROOT / "data"
RAW, MANUAL, INTERIM, META, CACHE, PROCESSED = (DATA / x for x in ("raw", "manual", "interim", "metadata", "cache", "processed"))
WEB = ROOT / "web"
WEB_DATA = WEB / "data"
WEB_LIVE = WEB_DATA / "live"
CONFIG = ROOT / "config"

REQUEST_DELAY = float(os.environ.get("SHAILDHARA_DELAY", "2"))
CONTACT = os.environ.get("SHAILDHARA_CONTACT", "").strip()
USER_AGENT = "SHAILDHARA-pipeline/1.0 (student research prototype" + (f"; contact: {CONTACT}" if CONTACT else "") + ")"
UA_TOKEN = "SHAILDHARA-pipeline"
MAX_DOWNLOAD_MB = int(os.environ.get("SHAILDHARA_MAX_MB", "2500"))


def ensure_dirs():
    for p in (RAW, MANUAL, INTERIM, META, CACHE, PROCESSED, WEB_DATA, WEB_LIVE, WEB_DATA / "network", WEB_DATA / "exposure"):
        p.mkdir(parents=True, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat(timespec="seconds")


def short(e, n=300):
    s = f"{type(e).__name__}: {e}"
    return s if len(s) <= n else s[:n] + "..."


def log(msg=""):
    line = f"[{utc_now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        META.mkdir(parents=True, exist_ok=True)
        with open(META / "pipeline_log.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config(name):
    with open(CONFIG / name, "r", encoding="utf-8") as f:
        return json.load(f)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _clean(obj):
    """Make an object strictly valid JSON: NaN/Infinity -> null, numpy scalars -> python, whole floats stay floats."""
    import math
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            obj = obj.item()
        elif isinstance(obj, np.ndarray):
            return _clean(obj.tolist())
    except ImportError:
        pass
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def save_json(path, obj, compact=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = _clean(obj)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str, allow_nan=False)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------- provenance
def record_source(entry):
    """Append one provenance record; publish_sources() merges them for the website."""
    entry = dict(entry)
    entry.setdefault("recorded_at", utc_iso())
    META.mkdir(parents=True, exist_ok=True)
    with open(META / "sources.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def publish_sources():
    latest = {}
    prev = read_json(WEB_DATA / "sources.json", {}) or {}
    for e in prev.get("sources", []):
        latest[e.get("source_id")] = e          # keep what was published before (e.g. static datasets in a live-only run)
    p = META / "sources.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                latest[e.get("source_id")] = e
            except Exception:
                pass
    rows = sorted(latest.values(), key=lambda r: str(r.get("source_id")))
    save_json(WEB_DATA / "sources.json", {"generated_at": utc_iso(), "sources": rows})
    return rows


# --------------------------------------------------------------------------- HTTP
class Http:
    def __init__(self, delay=REQUEST_DELAY):
        self.delay = delay
        self._last = 0.0
        self._robots = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def robots(self, url):
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            self._wait()
            try:
                r = self.session.get(host + "/robots.txt", timeout=(10, 20))
                if "x-deny-reason" in r.headers:
                    self._robots[host] = ("unreachable", "Blocked by this machine's network: " + r.headers["x-deny-reason"])
                elif r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self._robots[host] = ("parsed", rp)
                elif r.status_code in (401, 403):
                    self._robots[host] = ("disallowed", f"robots.txt returned HTTP {r.status_code}; treated as no automated access")
                elif 400 <= r.status_code < 500:
                    self._robots[host] = ("allowed", "")
                else:
                    self._robots[host] = ("indeterminate", f"robots.txt returned HTTP {r.status_code}; skipped to be safe")
            except requests.exceptions.RequestException as e:
                self._robots[host] = ("unreachable", "Could not reach the server: " + short(e))
        state, obj = self._robots[host]
        if state == "parsed":
            return ("allowed", "") if obj.can_fetch(UA_TOKEN, url) else ("disallowed", "robots.txt disallows automated access to this address")
        return state, obj

    def get(self, url, params=None, headers=None, etag=None, max_bytes=50_000_000, timeout=(10, 60)):
        """One polite GET. Never raises. Returns dict: ok, status, headers, content, error, skipped, truncated."""
        out = {"ok": False, "skipped": False, "unreachable": False, "status": None, "headers": {},
               "content": b"", "error": "", "truncated": False, "final_url": url}
        cur, cur_params = url, params
        for _ in range(4):
            verdict, why = self.robots(cur)
            if verdict in ("disallowed", "indeterminate"):
                out.update(skipped=True, error=why)
                return out
            if verdict == "unreachable":
                out.update(unreachable=True, error=why)
                return out
            self._wait()
            h = dict(headers or {})
            if etag:
                h["If-None-Match"] = etag
            try:
                r = self.session.get(cur, params=cur_params, headers=h, timeout=timeout, stream=True, allow_redirects=False)
            except requests.exceptions.SSLError as e:
                out.update(unreachable=True, error="SSL certificate problem (checks are never switched off): " + short(e))
                return out
            except requests.exceptions.RequestException as e:
                out.update(unreachable=True, error="Connection failed: " + short(e))
                return out
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                cur, cur_params = urljoin(cur, r.headers["Location"]), None
                r.close()
                continue
            out.update(status=r.status_code, headers=dict(r.headers), final_url=cur)
            if "x-deny-reason" in r.headers:
                out.update(unreachable=True, error="Blocked by this machine's network: " + r.headers["x-deny-reason"])
                r.close()
                return out
            chunks, total = [], 0
            for chunk in r.iter_content(1 << 16):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    out["truncated"] = True
                    break
            r.close()
            out["content"] = b"".join(chunks)[:max_bytes]
            out["ok"] = True
            return out
        out.update(unreachable=True, error="Too many redirects")
        return out

    def download(self, url, dest, max_mb=MAX_DOWNLOAD_MB):
        """Stream a file to disk. Returns dict like get() but with 'path' instead of content."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ok": False, "skipped": False, "unreachable": False, "status": None, "headers": {}, "error": "", "path": None}
        cur = url
        for _ in range(4):
            verdict, why = self.robots(cur)
            if verdict in ("disallowed", "indeterminate"):
                rec.update(skipped=True, error=why)
                return rec
            if verdict == "unreachable":
                rec.update(unreachable=True, error=why)
                return rec
            self._wait()
            try:
                r = self.session.get(cur, timeout=(10, 120), stream=True, allow_redirects=False)
            except requests.exceptions.SSLError as e:
                rec.update(unreachable=True, error="SSL certificate problem (checks are never switched off): " + short(e))
                return rec
            except requests.exceptions.RequestException as e:
                rec.update(unreachable=True, error="Connection failed: " + short(e))
                return rec
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                cur = urljoin(cur, r.headers["Location"])
                r.close()
                continue
            rec.update(status=r.status_code, headers=dict(r.headers))
            if "x-deny-reason" in r.headers:
                rec.update(unreachable=True, error="Blocked by this machine's network: " + r.headers["x-deny-reason"])
                r.close()
                return rec
            if r.status_code != 200:
                rec["error"] = f"HTTP {r.status_code}. Not downloaded; no retry or workaround."
                r.close()
                return rec
            part = dest.with_name(dest.name + ".part")
            total, first, too_big = 0, b"", False
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if not first:
                        first = chunk[:600]
                    total += len(chunk)
                    if total > max_mb * 1024 * 1024:
                        too_big = True
                        break
                    f.write(chunk)
            r.close()
            if too_big:
                part.unlink(missing_ok=True)
                rec["error"] = f"File larger than the {max_mb} MB safety limit"
                return rec
            low = first.lstrip()[:300].lower()
            if low.startswith(b"<") and (b"<html" in low or b"<!doctype" in low):
                part.unlink(missing_ok=True)
                rec["error"] = "The server sent a web page (HTML) instead of data (login or error page likely)."
                return rec
            os.replace(part, dest)
            rec.update(ok=True, path=dest)
            return rec
        rec.update(unreachable=True, error="Too many redirects")
        return rec


def normalise_name(s):
    s = (s or "").lower()
    s = re.sub(r"\b(district|dist\.?|zila|jila)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
