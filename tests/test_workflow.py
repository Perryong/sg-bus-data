from pathlib import Path

import yaml

WF = yaml.safe_load(Path(".github/workflows/update.yml").read_text())
STEPS = WF["jobs"]["update"]["steps"]


def idx(needle):
    return next(i for i, s in enumerate(STEPS) if needle in s.get("run", ""))


def test_triggers():
    on = WF.get(True, WF.get("on"))  # PyYAML parses the `on:` key as boolean True
    assert on["schedule"] == [{"cron": "0 19 * * 0"}]
    assert "workflow_dispatch" in on


def test_permissions_and_concurrency():
    assert WF["permissions"]["contents"] == "write"
    assert WF["concurrency"] == {"group": "update-data", "cancel-in-progress": False}


def test_tests_run_before_update():
    assert idx("uv run pytest -q") < idx("uv run python scripts/update.py")


def test_only_lta_key_secret():
    step = STEPS[idx("uv run python scripts/update.py")]
    assert step["env"] == {"LTA_KEY": "${{ secrets.LTA_KEY }}"}
    assert Path(".github/workflows/update.yml").read_text().count("secrets.") == 1


def test_commit_step():
    run = STEPS[idx("git push")]["run"]
    assert "git status --porcelain data/" in run
    assert "github-actions[bot]" in run
    assert 'data: update $(TZ=Asia/Singapore date +%F)' in run
    assert run.index("git pull --rebase") < run.index("git push")
