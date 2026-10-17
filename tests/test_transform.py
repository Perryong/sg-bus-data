import json
import random
from pathlib import Path

import pytest

from scripts.transform import build, check, dumps, natural_key

FX = Path(__file__).parent / "fixtures"


def load():
    return [json.loads((FX / f"{n}.json").read_text()) for n in ("BusStops", "BusRoutes", "BusServices")]


def test_stops():
    out = build(*load())["stops.json"]
    assert [s["code"] for s in out] == ["01012", "01013", "01014"]
    assert out[0] == {"code": "01012", "name": "A", "road": "Rd 01012", "lat": 1.29684825487647, "lon": 103.85253591654006}


def test_services():
    out = build(*load())["services.json"]
    assert list(out) == ["2", "10"]  # service 99 has no route rows: omitted
    ten = out["10"]
    assert (ten["operator"], ten["category"]) == ("SBST", "TRUNK")
    d1, d2 = ten["directions"]
    assert (d1["dir"], d1["from"], d1["to"], d1["loop"]) == (1, "01012", "01014", "")
    assert d1["stops"] == [["01012", 0], ["01013", 0.6], ["01014", 1.5]]
    assert d1["freq"] == {"am_peak": "07-13", "am_off": "09-17", "pm_peak": "11-17", "pm_off": "14-20"}
    assert (d2["dir"], d2["to"]) == (2, "01013")
    assert out["2"]["directions"][0]["loop"] == "Loop at A"


def test_first_last():
    fl = build(*load())["first_last.json"]
    assert fl["10/1"]["01012"] == {"wd": ["0500", "2300"], "sat": ["0530", "2330"], "sun": ["-", "-"]}
    assert set(fl) == {"10/1", "10/2", "2/1"}


def test_duplicates_ignored():
    stops, routes, services = load()
    assert build(stops + stops, routes + routes, services + services) == build(stops, routes, services)


def test_route_only_service_and_missing_fields():
    stops, routes, services = load()
    out = build(stops, routes, [s for s in services if s["ServiceNo"] != "2"])["services.json"]
    d = out["2"]["directions"][0]
    assert d["freq"] == dict.fromkeys(("am_peak", "am_off", "pm_peak", "pm_off"), "-")
    assert (out["2"]["operator"], out["2"]["category"], d["loop"]) == ("SBST", "", "")
    assert (d["from"], d["to"]) == ("01012", "01012")  # first/last route stop


def test_natural_key():
    assert sorted(["10", "2", "2A", "NR1", "100", "10A"], key=natural_key) == ["2", "2A", "10", "10A", "100", "NR1"]


def test_check_floors():
    stops, routes, services = load()
    with pytest.raises(ValueError, match="stops"):
        check([{}] * 4499, routes, services)
    with pytest.raises(ValueError, match="stops"):
        check(stops, routes, services, floors=(4, 1, 1))
    with pytest.raises(ValueError, match="services"):
        check(stops, routes, services, floors=(3, 5, 1))
    with pytest.raises(ValueError, match="route rows"):
        check(stops, routes, services, floors=(3, 1, 99))
    assert check(stops, routes, services, floors=(3, 2, 8)) == (routes, 0)


def test_check_unknown_stop_rule():
    stops, _, services = load()
    row = {"ServiceNo": "10", "Direction": 1, "StopSequence": 1, "BusStopCode": "01012", "Distance": 0}

    def rows(bad):
        return [dict(row, BusStopCode="99999") if i < bad else row for i in range(100)]

    with pytest.raises(ValueError, match="unknown"):
        check(stops, rows(2), services, floors=(1, 1, 1))
    kept, dropped = check(stops, rows(1), services, floors=(1, 1, 1))
    assert (len(kept), dropped) == (99, 1)


def test_dumps_deterministic():
    stops, routes, services = load()
    a = dumps(build(stops, routes, services))
    for rows in (stops, routes, services):
        random.Random(1).shuffle(rows)
    assert dumps(build(stops, routes, services)) == a
    s = dumps({"b": [1, 2], "a": "é"})
    assert s == '{"a":"é","b":[1,2]}\n'


def test_check_requires_bus_services():
    stops, routes, services = load()
    with pytest.raises(ValueError, match="BusServices"):
        check(stops, routes, [], floors=(3, 2, 8))
    with pytest.raises(ValueError, match="BusServices"):
        check(stops, routes, services[:1], floors=(3, 2, 8))


def test_conflicting_duplicates_stable():
    stops, routes, services = load()
    bad = dict(routes[1], BusStopCode="01014", Distance=9.9)  # same svc/dir/seq as routes[1]
    a = dumps(build(stops, routes + [bad], services))
    for seed in range(5):
        rows = routes + [bad]
        random.Random(seed).shuffle(rows)
        assert dumps(build(stops, rows, services)) == a


def test_raw_service_no_not_merged_and_nulls():
    stops, routes, services = load()
    r = dict(routes[0], ServiceNo="010", WD_FirstBus=None)
    s = dict(services[0], LoopDesc=None, AM_Peak_Freq=None, Category=None)
    out = build(stops, routes + [r], [s] + services[1:])
    assert {"10", "010"} <= set(out["services.json"])
    d = out["services.json"]["10"]["directions"][0]
    assert (d["loop"], d["freq"]["am_peak"], out["services.json"]["10"]["category"]) == ("", "-", "")
    assert out["first_last.json"]["010/1"]["01012"]["wd"] == ["-", "2300"]


def test_more_nulls_and_null_distance():
    stops, routes, services = load()
    stops = [dict(stops[0], Description=None, RoadName=None)] + stops[1:]
    routes = [dict(r, Operator=None) for r in routes if r["ServiceNo"] == "2"]
    services = [dict(s, Operator=None) for s in services]
    out = build(stops, routes, services)
    s0 = next(s for s in out["stops.json"] if s["code"] == "01014")
    assert (s0["name"], s0["road"], out["services.json"]["2"]["operator"]) == ("", "", "")
    _, routes, services = load()
    rows = [dict(routes[0], Distance=None)] + [dict(routes[1], BusStopCode="99999")] + routes[2:] * 20
    with pytest.raises(ValueError, match="unknown"):
        check(stops, rows, services, floors=(1, 1, 1))
    rows = routes * 13 + [dict(routes[0], Distance=None)]
    kept, dropped = check(stops, rows, services, floors=(1, 1, 1))
    assert dropped == 1 and all(r["Distance"] is not None for r in kept)


def test_loop_terminus_first_visit_wins():
    stops, routes, services = load()
    routes = [dict(r, WD_FirstBus="0999") if (r["ServiceNo"], r["StopSequence"]) == ("2", 3) else r for r in routes]
    assert build(stops, routes, services)["first_last.json"]["2/1"]["01012"]["wd"] == ["0500", "2300"]
