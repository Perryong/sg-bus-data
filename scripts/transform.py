"""Pure transform from LTA DataMall rows to the published JSON files (no I/O)."""
import json
import re


def natural_key(no: str) -> tuple:
    m = re.match(r"(\d+)(.*)", no)
    return (0, int(m[1]), m[2]) if m else (1, 0, no)


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n"


def check(stops, routes, services, floors=(4500, 500, 20000)):
    """Return (route rows kept, number dropped for unknown stops); raise ValueError on any failure."""
    min_stops, min_services, min_rows = floors
    if len(stops) < min_stops:
        raise ValueError(f"too few stops: {len(stops)} < {min_stops}")
    known = {s["BusStopCode"] for s in stops}
    # A null/missing Distance counts like an unknown stop: a fake 0 would corrupt ride times.
    kept = [r for r in routes if r["BusStopCode"] in known and r.get("Distance") is not None]
    dropped = len(routes) - len(kept)
    if dropped * 100 > len(routes):
        raise ValueError(f"too many route rows with unknown stops or no distance: {dropped} of {len(routes)}")
    n_services = len({r["ServiceNo"] for r in kept})
    if n_services < min_services:
        raise ValueError(f"too few services: {n_services} < {min_services}")
    n_listed = len({s["ServiceNo"] for s in services})
    if n_listed < min_services:
        raise ValueError(f"too few services in BusServices: {n_listed} < {min_services}")
    if len(kept) < min_rows:
        raise ValueError(f"too few route rows: {len(kept)} < {min_rows}")
    return kept, dropped


def _dedup(rows, key):
    # Sort first so which duplicate wins never depends on input order.
    out = {}
    for r in sorted(rows, key=lambda r: (key(r), json.dumps(r, sort_keys=True))):
        out.setdefault(key(r), r)
    return list(out.values())


def build(stops, routes, services):
    stop_rows = _dedup(stops, lambda s: s["BusStopCode"])
    route_rows = _dedup(routes, lambda r: (r["ServiceNo"], r["Direction"], r["StopSequence"]))
    svc_rows = _dedup(services, lambda s: (s["ServiceNo"], s["Direction"]))
    info = {(s["ServiceNo"], s["Direction"]): s for s in svc_rows}

    grouped: dict[str, dict[int, list]] = {}
    for r in route_rows:  # already in (service, dir, sequence) order
        grouped.setdefault(r["ServiceNo"], {}).setdefault(r["Direction"], []).append(r)

    out_services, first_last = {}, {}
    for no, dirs in grouped.items():
        svc_info = [info[(no, d)] for d in dirs if (no, d) in info]
        first = next(iter(dirs.values()))[0]
        operator = next((i["Operator"] for i in svc_info if i.get("Operator")), None) or first.get("Operator") or ""
        directions = []
        for d, rows in dirs.items():
            i = info.get((no, d), {})
            directions.append({
                "dir": d,
                "from": i.get("OriginCode") or rows[0]["BusStopCode"],
                "to": i.get("DestinationCode") or rows[-1]["BusStopCode"],
                "loop": i.get("LoopDesc") or "",
                "stops": [[r["BusStopCode"], r["Distance"]] for r in rows],
                "freq": {k: i.get(f) or "-" for k, f in (
                    ("am_peak", "AM_Peak_Freq"), ("am_off", "AM_Offpeak_Freq"),
                    ("pm_peak", "PM_Peak_Freq"), ("pm_off", "PM_Offpeak_Freq"))},
            })
            # A stop visited twice in a direction (loop terminus): the FIRST visit's (lowest StopSequence) times win, i.e. the departure riders board.
            first_last[f"{no}/{d}"] = {
                r["BusStopCode"]: {p: [r[f"{k}_FirstBus"] or "-", r[f"{k}_LastBus"] or "-"]
                                   for p, k in (("wd", "WD"), ("sat", "SAT"), ("sun", "SUN"))}
                for r in reversed(rows)}
        out_services[no] = {
            "operator": operator,
            "category": (svc_info[0].get("Category") or "" if svc_info else ""),
            "directions": directions,
        }

    return {
        "stops.json": [{"code": s["BusStopCode"], "name": s["Description"] or "", "road": s["RoadName"] or "",
                        "lat": s["Latitude"], "lon": s["Longitude"]} for s in stop_rows],
        "services.json": dict(sorted(out_services.items(), key=lambda kv: natural_key(kv[0]))),
        "first_last.json": first_last,
    }
