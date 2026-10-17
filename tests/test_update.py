import json
import os
import shutil
from pathlib import Path

import httpx
import pytest

from scripts import update
from scripts.transform import build, dumps

FX = Path(__file__).parent / "fixtures"
NAMES = ("BusStops", "BusRoutes", "BusServices")
KEY = "SECRET-KEY-123"


def rows():
    return {n: json.loads((FX / f"{n}.json").read_text()) for n in NAMES}


@pytest.fixture(autouse=True)
def tame(monkeypatch):
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    monkeypatch.setattr(update, "FLOORS", (1, 1, 1))
    monkeypatch.setenv("LTA_KEY", KEY)


def fake_pages(monkeypatch, data, fails=None):
    """Fake the per-page HTTP call: pages of 500 from `data`; fails[dataset] = attempts that raise first."""
    fails = dict(fails or {})

    def page(dataset, skip, key):
        if fails.get(dataset, 0) > 0:
            fails[dataset] -= 1
            raise RuntimeError(f"{dataset}: HTTP 500")
        return data[dataset][skip:skip + 500]

    monkeypatch.setattr(update, "_page", page)


def test_paging_and_retry(monkeypatch):
    data = {"X": [{"i": i} for i in range(1100)]}
    fake_pages(monkeypatch, data, {"X": 2})
    assert update.fetch_all("X", KEY) == data["X"]


def test_fetch_gives_up_after_retries(monkeypatch, tmp_path, capsys):
    fake_pages(monkeypatch, rows(), {"BusRoutes": 4})
    assert update.main(["--out", str(tmp_path / "data")]) == 1
    assert not (tmp_path / "data").exists()
    assert "BusRoutes: HTTP 500" in capsys.readouterr().err


def test_missing_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("LTA_KEY")
    monkeypatch.setattr(update, "ROOT", tmp_path)
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    assert "set LTA_KEY" in capsys.readouterr().err


def test_lta_key_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("LTA_KEY")
    monkeypatch.setattr(update, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("# c\nexport LTA_KEY='abc'\n")
    assert update.lta_key() == "abc"


def test_write_unchanged_and_changed(monkeypatch, tmp_path):
    out = tmp_path / "data"
    r = rows()
    fake_pages(monkeypatch, r)
    assert update.main(["--out", str(out)]) == 0
    expected = build(r["BusStops"], r["BusRoutes"], r["BusServices"])
    for name, obj in expected.items():
        assert (out / name).read_bytes() == dumps(obj).encode()
    meta = json.loads((out / "meta.json").read_text())
    assert meta["counts"]["stops"] == 3 and meta["updated"].endswith("+08:00")
    assert sorted(p.name for p in out.iterdir()) == ["first_last.json", "meta.json", "services.json", "stops.json"]

    old = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}
    assert update.main(["--out", str(out)]) == 0
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()} == old

    r["BusStops"][0] = {**r["BusStops"][0], "Description": "Renamed"}
    fake_pages(monkeypatch, r)
    assert update.main(["--out", str(out)]) == 0
    assert (out / "stops.json").read_bytes() != old["stops.json"][0]
    assert (out / "services.json").stat().st_mtime_ns == old["services.json"][1]
    assert (out / "meta.json").stat().st_mtime_ns != old["meta.json"][1]


def test_key_never_leaks(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        return httpx.Response(500, request=httpx.Request("GET", "https://x/?"))

    monkeypatch.setattr(update.httpx, "get", boom)
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    cap = capsys.readouterr()
    assert KEY not in cap.out + cap.err and "BusStops: HTTP 500" in cap.err
    assert not (tmp_path / "d").exists()

    def netfail(*a, **k):
        raise httpx.ConnectError(f"boom {k['headers']}")

    monkeypatch.setattr(update.httpx, "get", netfail)
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    cap = capsys.readouterr()
    assert KEY not in cap.out + cap.err and "BusStops: network" in cap.err


def test_from_dir(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FX, src)
    assert update.main(["--from-dir", str(src), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "stops.json").exists()


def test_missing_meta_recreated(monkeypatch, tmp_path):
    out = tmp_path / "data"
    fake_pages(monkeypatch, rows())
    assert update.main(["--out", str(out)]) == 0
    (out / "meta.json").unlink()
    assert update.main(["--out", str(out)]) == 0
    assert (out / "meta.json").exists()


def test_4xx_not_retried_5xx_is_(monkeypatch, tmp_path):
    calls = []

    def get(*a, **k):
        calls.append(1)
        return httpx.Response(calls_status[0], request=httpx.Request("GET", "https://x/"))

    monkeypatch.setattr(update.httpx, "get", get)
    calls_status = [401]
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    assert len(calls) == 1
    calls.clear()
    calls_status[0] = 503
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    assert len(calls) == update.RETRIES + 1


def test_malformed_rows(monkeypatch, tmp_path, capsys):
    r = rows()
    r["BusRoutes"][0] = {"ServiceNo": "10"}
    fake_pages(monkeypatch, r)
    assert update.main(["--out", str(tmp_path / "d")]) == 1
    assert "malformed" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()


def synth(n):
    stops = [{"BusStopCode": f"{i:05d}", "RoadName": "R", "Description": f"S{i}", "Latitude": 1.3, "Longitude": 103.8}
             for i in range(n)]
    routes = [{**rows()["BusRoutes"][0], "ServiceNo": f"s{i}", "BusStopCode": f"{i:05d}"} for i in range(n)]
    services = [{**rows()["BusServices"][0], "ServiceNo": f"s{i}"} for i in range(n)]
    return {"BusStops": stops, "BusRoutes": routes, "BusServices": services}


def test_unknown_stop_row_never_published(monkeypatch, tmp_path):
    r = synth(200)
    r["BusRoutes"].append({**r["BusRoutes"][0], "ServiceNo": "BAD", "BusStopCode": "99999"})
    r["BusServices"].append({**r["BusServices"][0], "ServiceNo": "BAD"})
    fake_pages(monkeypatch, r)
    out = tmp_path / "d"
    assert update.main(["--out", str(out)]) == 0
    assert "BAD" not in json.loads((out / "services.json").read_text())
    assert not any(k.startswith("BAD/") for k in json.loads((out / "first_last.json").read_text()))
    assert json.loads((out / "meta.json").read_text())["counts"] == {"route_rows": 200, "services": 200, "stops": 200}


@pytest.mark.parametrize("n,flags,code", [(94, [], 1), (96, [], 0), (94, ["--allow-drop"], 0)])
def test_drop_guard(monkeypatch, tmp_path, capsys, n, flags, code):
    out = tmp_path / "d"
    fake_pages(monkeypatch, synth(100))
    assert update.main(["--out", str(out)]) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    fake_pages(monkeypatch, synth(n))
    assert update.main(["--out", str(out), *flags]) == code
    after = {p.name: p.read_bytes() for p in out.iterdir()}
    assert (after == before) == (code == 1)
    if code:
        assert "dropped 6% vs last run" in capsys.readouterr().err
