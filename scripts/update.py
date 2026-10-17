"""Fetch LTA DataMall bus data, check it, and rewrite data/ only for files whose content changed.

usage: uv run python scripts/update.py [--from-dir DIR] [--out DIR] [--allow-drop]
Prints counts or short errors only (dataset + HTTP status); never request details or the key.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so `python scripts/update.py` finds the scripts package

from scripts.transform import build, check, dumps  # noqa: E402
BASE = "https://datamall2.mytransport.sg/ltaodataservice/"
DATASETS = ("BusStops", "BusRoutes", "BusServices")
FLOORS = (4500, 500, 20000)
RETRIES = 3


def lta_key():
    if os.environ.get("LTA_KEY"):
        return os.environ["LTA_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if line.startswith("LTA_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class Fatal(RuntimeError):
    """An error retrying cannot fix (HTTP 4xx, e.g. a bad key)."""


def _page(dataset, skip, key):
    try:
        r = httpx.get(f"{BASE}{dataset}?$skip={skip}", timeout=60,
                      headers={"AccountKey": key, "accept": "application/json"})
    except httpx.HTTPError:  # message may echo request details: say nothing about it
        raise RuntimeError(f"{dataset}: network") from None
    if r.status_code != 200:
        raise (Fatal if 400 <= r.status_code < 500 else RuntimeError)(f"{dataset}: HTTP {r.status_code}")
    try:
        return r.json()["value"]
    except (ValueError, KeyError):
        raise RuntimeError(f"{dataset}: bad response") from None


def fetch_all(dataset, key):
    rows, skip = [], 0
    while True:
        for attempt in range(RETRIES + 1):
            try:
                page = _page(dataset, skip, key)
                break
            except RuntimeError as e:
                if isinstance(e, Fatal) or attempt == RETRIES:
                    raise
                time.sleep(2 ** attempt)
        if not page:
            return rows
        rows += page
        skip += 500


def _write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir")
    ap.add_argument("--out", default=str(ROOT / "data"))
    ap.add_argument("--allow-drop", action="store_true", help="accept a >5%% fall in counts vs the last run")
    args = ap.parse_args(argv)
    try:
        if args.from_dir:
            src = Path(args.from_dir)
            got = {n: json.loads((src / f"{n}.json").read_text(encoding="utf-8")) for n in DATASETS}
        else:
            key = lta_key()
            if not key:
                raise RuntimeError("set LTA_KEY (env or .env)")
            got = {n: fetch_all(n, key) for n in DATASETS}
        stops, routes, services = (got[n] for n in DATASETS)
        kept, dropped = check(stops, routes, services, FLOORS)
        files = build(stops, kept, services)  # kept: dropped rows are never published
    except (RuntimeError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (KeyError, TypeError) as e:
        print(f"error: malformed rows ({type(e).__name__})", file=sys.stderr)
        return 1
    counts = {"stops": len(files["stops.json"]), "services": len(files["services.json"]), "route_rows": len(kept)}
    out = Path(args.out)
    if (out / "meta.json").exists() and not args.allow_drop:
        try:
            last = json.loads((out / "meta.json").read_text(encoding="utf-8"))["counts"]
            for k, v in counts.items():
                if v < last[k] * 0.95:
                    print(f"error: {k} dropped {round(100 - 100 * v / last[k])}% vs last run "
                          f"({last[k]} -> {v}); rerun with --allow-drop if intended", file=sys.stderr)
                    return 1
        except (ValueError, KeyError, TypeError, OSError):
            pass  # unreadable meta: nothing to compare with
    new = {name: dumps(obj) for name, obj in files.items()}
    out.mkdir(parents=True, exist_ok=True)
    changed = [n for n, text in new.items()
               if not (out / n).exists() or (out / n).read_text(encoding="utf-8") != text]
    if changed or not (out / "meta.json").exists():
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        meta = {"updated": now, "counts": counts, "source": "LTA DataMall",
                "licence": "Singapore Open Data Licence"}
        for n in changed:
            _write(out / n, new[n])
        _write(out / "meta.json", dumps(meta))
    print(f"stops={counts['stops']} services={counts['services']} route_rows={counts['route_rows']} "
          f"dropped={dropped} " + (f"changed={','.join(changed)}" if changed else "no change"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
