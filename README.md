# sg-bus-data

Singapore bus stops, routes, frequencies and first/last bus times from LTA DataMall, republished as small,
documented JSON files and refreshed weekly. The code is MIT; the data is LTA's.

> Contains information from LTA DataMall, made available under the
> [Singapore Open Data Licence version 1.0](https://data.gov.sg/open-data-licence).
> This project is not affiliated with or endorsed by LTA.

## Files

Stable URLs, for `<file>` in `stops.json`, `services.json`, `first_last.json`, `meta.json`:

- `https://raw.githubusercontent.com/Perryong/sg-bus-data/main/data/<file>` (always the latest commit)
- `https://cdn.jsdelivr.net/gh/Perryong/sg-bus-data@main/data/<file>` (CDN; it may cache a branch for hours)

JSON object key order is not meaningful (keys are sorted so unchanged data gives identical files and clean diffs).
Lists are ordered as described below.

### `stops.json`
List sorted by `code`; `lat`/`lon` exactly as LTA gives them.
```json
{"code":"01012","lat":1.29684825487647,"lon":103.85253591654006,"name":"Hotel Grand Pacific","road":"Victoria St"}
```

### `services.json`
Object keyed by service number. Keys are sorted as plain strings (10, 100, 100A, 101, ..., 2) for stable diffs, so order services yourself if needed (e.g. by leading number, then suffix). Each has `operator`,
`category` and `directions` (ordered by `dir`). `from`/`to` are stop codes, `loop` is LTA's `LoopDesc` or `""`,
`stops` is `[stop code, cumulative distance in km]` in route order, and `freq` holds LTA's frequency bands in minutes
as strings (`"06-09"`, or `"-"` for no service).
```json
{"10":{"category":"TRUNK","operator":"GAS","directions":[{"dir":1,"from":"75009","to":"16009","loop":"",
 "stops":[["75009",0],["76059",0.6]],
 "freq":{"am_peak":"09-10","am_off":"09-16","pm_peak":"11-16","pm_off":"16-18"}}]}}
```

### `first_last.json`
Keyed by `"<service>/<dir>"`, then stop code; `[first, last]` bus as LTA's `"HHMM"` strings (`"-"` if none) for
weekday (`wd`), Saturday (`sat`) and Sunday/holiday (`sun`).
```json
{"10/1":{"02119":{"wd":["0553","2351"],"sat":["0551","2352"],"sun":["0551","2352"]}}}
```

### `meta.json`
```json
{"counts":{"route_rows":26833,"services":601,"stops":5209},"licence":"Singapore Open Data Licence","source":"LTA DataMall","updated":"2026-10-17T09:56:46+08:00"}
```
Only rewritten when another file changed, so `updated` is when the data last changed.

## Update schedule

A GitHub Action runs every Monday 03:00 SGT (and on demand), and commits `data/` only if something changed. The
update fails, committing nothing, if LTA returns too little (fewer than 4,500 stops, 500 services or 20,000 route
rows), more than 1% of route rows point at unknown stops, or stops, services or route rows fall more than 5% from
the last run (a likely partial download; pass `--allow-drop` if the drop is real), so the last good data stays published.

Run it now: Actions → update data → Run workflow.

## Setup (owner/forks)

- Add a repository secret `LTA_KEY` (your DataMall key) under Settings → Secrets and variables → Actions.
- The default branch must accept pushes from `github-actions` (no branch protection blocking the bot).
- GitHub disables scheduled workflows after 60 days without repo activity in public repos; if that happens,
  re-enable the workflow under Actions.

## Run locally

Needs [uv](https://docs.astral.sh/uv/) and a free [LTA DataMall](https://datamall.lta.gov.sg) key
(`LTA_KEY` in the environment, or in a git-ignored `.env`; see `.env.example`).

```
uv run python scripts/update.py                      # fetch and update data/
uv run python scripts/update.py --from-dir DIR       # use cached BusStops.json, BusRoutes.json, BusServices.json
uv run python scripts/update.py --out DIR            # write somewhere other than data/
uv run python scripts/update.py --allow-drop         # accept a >5% fall in counts vs the last run
uv run pytest -q
```
