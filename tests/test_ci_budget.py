"""The CI budget check must be able to go red, and must not pass on absent data."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_ci_budget", ROOT / ".github/scripts/check_ci_budget.py"
)
assert _SPEC and _SPEC.loader
check_ci_budget = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_ci_budget)


def _run(started: str, finished: str) -> dict[str, str]:
    return {"run_started_at": started, "updated_at": finished}


def test_run_seconds_measures_wall_clock() -> None:
    run = _run("2026-09-12T16:10:02Z", "2026-09-12T16:13:06Z")
    assert check_ci_budget.run_seconds(run) == 184.0


@pytest.mark.parametrize(
    "run",
    [
        {"updated_at": "2026-09-12T16:13:06Z"},
        {"run_started_at": "2026-09-12T16:10:02Z"},
        _run("not a timestamp", "2026-09-12T16:13:06Z"),
        _run("2026-09-12T16:13:06Z", "2026-09-12T16:10:02Z"),
    ],
)
def test_unusable_runs_are_not_measured(run: dict[str, str]) -> None:
    """A malformed or negative duration must be dropped, never counted as fast."""
    assert check_ci_budget.run_seconds(run) is None


def test_median_uses_only_the_most_recent_sample() -> None:
    runs = [
        _run("2026-09-12T16:00:00Z", "2026-09-12T16:01:00Z"),
        _run("2026-09-12T15:00:00Z", "2026-09-12T15:05:00Z"),
        _run("2026-09-12T14:00:00Z", "2026-09-12T14:03:00Z"),
        _run("2026-09-12T13:00:00Z", "2026-09-12T13:59:00Z"),
    ]
    assert check_ci_budget.median_seconds(runs, 3) == 180.0
    assert check_ci_budget.median_seconds(runs, 4) == 240.0


def test_median_of_no_usable_runs_is_none() -> None:
    assert check_ci_budget.median_seconds([{"updated_at": "x"}], 5) is None


def test_median_rejects_a_meaningless_sample_size() -> None:
    with pytest.raises(ValueError):
        check_ci_budget.median_seconds([], 0)


def test_a_workflow_over_budget_is_reported_over() -> None:
    rows = check_ci_budget.verdicts({"Python tests": 151.0}, {"Python tests": 150})
    assert rows == [("over", "Python tests", 151.0, 150)]


def test_a_workflow_exactly_at_budget_passes() -> None:
    rows = check_ci_budget.verdicts({"Python tests": 150.0}, {"Python tests": 150})
    assert rows == [("ok", "Python tests", 150.0, 150)]


def test_absence_of_data_is_not_a_pass() -> None:
    """A workflow that produced no runs must not be silently credited."""
    rows = check_ci_budget.verdicts({"Python tests": None}, {"Python tests": 150})
    assert rows == [("no-data", "Python tests", None, 150)]


# Source-text reason: these YAML files and the budget JSON are the artifacts
# consumed by GitHub Actions; their trigger/concurrency expressions are the contract.
def test_budget_file_covers_every_workflow_that_runs_on_push() -> None:
    """A new push-triggered workflow must arrive with a budget, not without one."""
    import yaml

    config = json.loads((ROOT / ".github/ci-budget.json").read_text(encoding="utf-8"))
    budgeted = set(config["budgets"])
    on_push = set()
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow.get(True, workflow.get("on", {}))
        if isinstance(triggers, dict) and "push" in triggers:
            on_push.add(workflow["name"])
    assert on_push == budgeted


def test_every_workflow_cancels_superseded_runs_off_main() -> None:
    """Without this, one release burns a runner for every ref it touches."""
    import yaml

    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = workflow.get(True, workflow.get("on", {}))
        if not (isinstance(triggers, dict) and "push" in triggers):
            continue
        concurrency = workflow.get("concurrency")
        assert concurrency, f"{path.name} has no concurrency group"
        assert "github.ref" in concurrency["group"], path.name
        assert "refs/heads/main" in str(concurrency["cancel-in-progress"]), path.name


def test_a_renamed_workflow_is_not_reported_as_slow() -> None:
    """The two failures need different repairs, so they cannot share a sentence."""
    (line,) = check_ci_budget.summary_lines(over=False, no_data=True)
    assert "slower" not in line
    assert "renamed" in line
    assert ".github/ci-budget.json" in line


def test_an_over_budget_workflow_names_the_cold_cache_cause() -> None:
    """A cold cache is a recurring cause, so a reader must not just raise the number."""
    (line,) = check_ci_budget.summary_lines(over=True, no_data=False)
    assert "cold-cache" in line
    assert "renamed" not in line


def test_both_failures_are_reported_separately() -> None:
    assert len(check_ci_budget.summary_lines(over=True, no_data=True)) == 2


def test_a_passing_run_says_nothing() -> None:
    assert check_ci_budget.summary_lines(over=False, no_data=False) == []


@pytest.mark.parametrize("history", ["quiet", "sparse", "empty", "unusable", "missing"])
def test_main_measures_each_workflows_own_usable_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    history: str,
) -> None:
    """Busy unrelated workflows cannot hide a quiet workflow or create a pass."""
    from urllib.parse import parse_qs, urlparse

    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"sample_size": 10, "budgets": {"Quiet": 120}}))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    pages = []
    fast = _run("2026-09-12T16:00:00Z", "2026-09-12T16:01:00Z")
    slow = _run("2026-09-12T16:00:00Z", "2026-09-12T16:59:00Z")

    def fetch(url: str, _token: str) -> dict:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/actions/workflows"):
            return {
                "workflows": []
                if history == "missing"
                else [{"name": "Quiet", "id": 7}]
            }
        if parsed.path.endswith("/actions/runs"):
            return {"workflow_runs": [fast | {"name": "Busy"}] * 100}
        assert parsed.path.endswith("/actions/workflows/7/runs")
        assert query["branch"] == ["main"]
        assert query["event"] == ["push"]
        assert query["status"] == ["success"]
        page = int(query["page"][0])
        pages.append(page)
        if history == "empty":
            return {"workflow_runs": []}
        if history == "unusable" or (history == "quiet" and page == 1):
            return {"workflow_runs": [{"run_started_at": "invalid"}] * 100}
        return {
            "workflow_runs": [fast] * (3 if history == "sparse" else 10)
            + ([slow] * 3 if history == "quiet" else [])
        }

    monkeypatch.setattr(check_ci_budget, "_fetch", fetch)
    result = check_ci_budget.main(
        ["--repository", "example/repo", "--budget-file", str(budget)]
    )
    output = capsys.readouterr().out
    if history in {"quiet", "sparse"}:
        assert result == 0
        assert "median 60s" in output
        assert ("10/10" if history == "quiet" else "3/10") in output
        assert pages == ([1, 2] if history == "quiet" else [1])
    else:
        assert result == 1
        assert "NO DATA" in output
        if history == "unusable":
            assert 1 <= len(pages) <= 10


def test_manual_and_scheduled_runs_do_not_count_toward_the_median(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A slow trial run on another runner must not push main's median over budget."""
    from urllib.parse import parse_qs, urlparse

    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"sample_size": 10, "budgets": {"Quiet": 120}}))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    push = _run("2026-09-12T16:00:00Z", "2026-09-12T16:01:00Z") | {"event": "push"}
    trial = _run("2026-09-12T16:00:00Z", "2026-09-12T16:30:00Z") | {
        "event": "workflow_dispatch"
    }

    def fetch(url: str, _token: str) -> dict:
        parsed = urlparse(url)
        if parsed.path.endswith("/actions/workflows"):
            return {"workflows": [{"name": "Quiet", "id": 7}]}
        # The API filters by event only when asked; otherwise it returns every event.
        event = parse_qs(parsed.query).get("event", [None])[0]
        runs = [push] * 3 + [trial] * 7
        return {"workflow_runs": [r for r in runs if event in (None, r["event"])]}

    monkeypatch.setattr(check_ci_budget, "_fetch", fetch)
    result = check_ci_budget.main(
        ["--repository", "example/repo", "--budget-file", str(budget)]
    )
    assert result == 0
    assert "median 60s" in capsys.readouterr().out


_NOW = check_ci_budget.datetime(2026, 11, 9, 12, 1, 0)


def test_this_run_over_its_budget_is_warned() -> None:
    warning = check_ci_budget.this_run_warning(
        "Hassfest", "2026-11-09T12:00:00Z", _NOW, {"Hassfest": 45}
    )
    assert warning is not None
    assert "Hassfest took 60s" in warning
    assert "45s" in warning


def test_this_run_exactly_at_its_budget_is_not_warned() -> None:
    warning = check_ci_budget.this_run_warning(
        "Hassfest", "2026-11-09T12:00:00Z", _NOW, {"Hassfest": 60}
    )
    assert warning is None


@pytest.mark.parametrize(
    ("workflow", "created_at"),
    [
        ("CI budget", "2026-11-09T12:00:00Z"),
        ("Hassfest", ""),
        ("Hassfest", "not a timestamp"),
        ("Hassfest", "2026-11-09T12:05:00Z"),
    ],
)
def test_this_run_without_a_usable_budget_or_time_is_not_warned(
    workflow: str, created_at: str
) -> None:
    """Advice only: what cannot be measured is left alone, never guessed at."""
    assert (
        check_ci_budget.this_run_warning(workflow, created_at, _NOW, {"Hassfest": 1})
        is None
    )


def _this_run_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetch: object
) -> tuple[Path, Path]:
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"sample_size": 10, "budgets": {"Hassfest": 45}}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Hassfest")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(check_ci_budget, "_fetch", fetch)
    return budget, summary


@pytest.mark.parametrize(("age", "warned"), [(3600, True), (5, False)])
def test_this_run_mode_annotates_only_a_slow_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    age: int,
    warned: bool,
) -> None:
    from datetime import UTC, datetime, timedelta

    created = (datetime.now(UTC) - timedelta(seconds=age)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    urls = []

    def fetch(url: str, _token: str) -> dict:
        urls.append(url)
        return {"created_at": created}

    budget, summary = _this_run_env(tmp_path, monkeypatch, fetch)
    result = check_ci_budget.main(
        ["--this-run", "--repository", "example/repo", "--budget-file", str(budget)]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert urls == ["https://api.github.com/repos/example/repo/actions/runs/99"]
    assert ("::warning title=CI budget::Hassfest took" in output) is warned
    assert (summary.exists() and "over its budget" in summary.read_text()) is warned


@pytest.mark.parametrize("failure", ["api", "token", "budget-file"])
def test_this_run_mode_never_fails_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """A warning-only step that can go red would turn a slow push into a failed one."""

    def fetch(_url: str, _token: str) -> dict:
        raise OSError("HTTP Error 502")

    budget, summary = _this_run_env(tmp_path, monkeypatch, fetch)
    if failure == "token":
        monkeypatch.delenv("GITHUB_TOKEN")
    if failure == "budget-file":
        budget.write_text("{not json")
    result = check_ci_budget.main(
        ["--this-run", "--repository", "example/repo", "--budget-file", str(budget)]
    )
    assert result == 0
    assert "not checked" in capsys.readouterr().out
    assert not summary.exists()


# Source-text reason: the workflow YAML is the artifact GitHub Actions runs; the
# job's trigger, runner, dependencies and permissions are the contract.
def test_every_budgeted_workflow_warns_on_its_own_slow_push() -> None:
    """A budget that is only checked weekly lets a slow week repeat unseen."""
    import yaml

    budgets = json.loads((ROOT / ".github/ci-budget.json").read_text(encoding="utf-8"))
    checked = set()
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        for name, job in jobs.items():
            steps = " ".join(step.get("run", "") for step in job.get("steps", []))
            if "check_ci_budget.py --this-run" not in steps:
                continue
            checked.add(workflow["name"])
            assert job["runs-on"] == "ubuntu-24.04", path.name
            assert job["if"].startswith("${{ always() && "), path.name
            assert "github.event_name == 'push'" in job["if"], path.name
            assert "github.ref == 'refs/heads/main'" in job["if"], path.name
            assert set(job["needs"]) == set(jobs) - {name}, path.name
            assert job["permissions"]["actions"] == "read", path.name
            assert all(step.get("continue-on-error") for step in job["steps"]), (
                path.name
            )
    assert checked == set(budgets["budgets"])


_RULE = {
    "budget_seconds": 10,
    "prefixes": ["Set up ", "Pull ", "Restore "],
    "exempt": {
        "Pull unavoidable": "pulled before the first step",
        "hosted / Restore slow": "no cache on this runner",
    },
}


def _step(name: str, seconds: int, conclusion: str = "success") -> dict[str, str]:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": "2026-11-10T00:00:00Z",
        "completed_at": f"2026-11-10T00:00:{seconds:02d}Z",
    }


def _job(name: str, *steps: dict[str, str], status: str = "completed") -> dict:
    return {"name": name, "status": status, "steps": list(steps)}


def test_a_slow_setup_step_is_reported_with_its_job() -> None:
    times = check_ci_budget.setup_step_times(
        [_job("tests · 1/2", _step("Set up Python", 12), _step("Run tests", 59))],
        _RULE,
    )
    assert check_ci_budget.slow_setup_steps([times], 10) == [
        ("tests · 1/2", "Set up Python", 12.0)
    ]


def test_a_setup_step_exactly_at_its_budget_passes() -> None:
    times = check_ci_budget.setup_step_times(
        [_job("tests", _step("Set up Python", 10))], _RULE
    )
    assert check_ci_budget.slow_setup_steps([times], 10) == []


def test_work_steps_and_exempt_steps_are_not_setup() -> None:
    """Only preparation is budgeted: not the tests, not a reasoned exemption."""
    times = check_ci_budget.setup_step_times(
        [_job("validate", _step("Pull unavoidable", 40), _step("Run tests", 50))],
        _RULE,
    )
    assert times == {}


def test_a_job_scoped_exemption_holds_only_in_that_job() -> None:
    """The same step can be cut on one runner and not on another."""
    times = check_ci_budget.setup_step_times(
        [
            _job("hosted", _step("Restore slow", 30)),
            _job("blacksmith", _step("Restore slow", 30)),
        ],
        _RULE,
    )
    assert times == {("blacksmith", "Restore slow"): 30.0}


def test_a_skipped_install_cost_nothing() -> None:
    """A cache hit skips its install step; that run counts as fast, not absent."""
    samples = [
        check_ci_budget.setup_step_times(
            [_job("tests", _step("Restore env", seconds, conclusion))], _RULE
        )
        for seconds, conclusion in [(50, "success"), (50, "skipped"), (50, "skipped")]
    ]
    assert check_ci_budget.slow_setup_steps(samples, 10) == []


def test_one_cold_cache_run_does_not_fail_the_weekly_check() -> None:
    samples = [{("tests", "Set up Python"): s} for s in (40.0, 2.0, 3.0)]
    assert check_ci_budget.slow_setup_steps(samples, 10) == []
    samples.append({("tests", "Set up Python"): 30.0})
    samples.append({("tests", "Set up Python"): 30.0})
    assert check_ci_budget.slow_setup_steps(samples, 10) == [
        ("tests", "Set up Python", 30.0)
    ]


def test_an_unfinished_job_is_left_out() -> None:
    """In --this-run the budget job itself is still running and has no verdict yet."""
    times = check_ci_budget.setup_step_times(
        [_job("budget", _step("Set up job", 30), status="in_progress")], _RULE
    )
    assert times == {}


def test_a_setup_warning_names_the_levers_before_an_exemption() -> None:
    warning = check_ci_budget.setup_step_warning("tests", "Set up Python", 12.0, 10)
    assert "'Set up Python'" in warning and "'tests'" in warning
    assert "12s" in warning and "10s" in warning
    assert warning.index("cache") < warning.index("exempt")


def test_this_run_mode_warns_on_a_slow_setup_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import UTC, datetime

    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    urls = []

    def fetch(url: str, _token: str) -> dict:
        urls.append(url)
        if "/jobs" in url:
            return {
                "jobs": [
                    _job("validate", _step("Set up Python", 12)),
                    _job("budget", _step("Set up job", 30), status="in_progress"),
                ]
            }
        return {"created_at": created}

    budget, summary = _this_run_env(tmp_path, monkeypatch, fetch)
    budget.write_text(
        json.dumps(
            {"sample_size": 10, "budgets": {"Hassfest": 45}, "setup_step": _RULE}
        )
    )
    result = check_ci_budget.main(
        ["--this-run", "--repository", "example/repo", "--budget-file", str(budget)]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert urls[1].endswith("/actions/runs/99/jobs?filter=latest&per_page=100")
    assert "::warning title=CI budget::Set-up step 'Set up Python'" in output
    assert "Set up job" not in output
    assert "Hassfest took" not in output
    assert "Set up Python" in summary.read_text()


@pytest.mark.parametrize(("seconds", "failed"), [(25, True), (4, False)])
def test_the_weekly_check_fails_on_a_slow_setup_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seconds: int,
    failed: bool,
) -> None:
    """A workflow inside its wall budget can still hide one download that grew."""
    from urllib.parse import urlparse

    budget = tmp_path / "budget.json"
    budget.write_text(
        json.dumps({"sample_size": 3, "budgets": {"Quiet": 120}, "setup_step": _RULE})
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fast = _run("2026-09-12T16:00:00Z", "2026-09-12T16:01:00Z")
    jobs_fetched = []

    def fetch(url: str, _token: str) -> dict:
        path = urlparse(url).path
        if path.endswith("/actions/workflows"):
            return {"workflows": [{"name": "Quiet", "id": 7}]}
        if path.endswith("/jobs"):
            jobs_fetched.append(path)
            return {"jobs": [_job("tests", _step("Set up Python", seconds))]}
        return {"workflow_runs": [fast | {"id": n} for n in range(3)]}

    monkeypatch.setattr(check_ci_budget, "_fetch", fetch)
    result = check_ci_budget.main(
        ["--repository", "example/repo", "--budget-file", str(budget)]
    )
    output = capsys.readouterr().out
    assert sorted(jobs_fetched) == [
        f"/repos/example/repo/actions/runs/{n}/jobs" for n in range(3)
    ]
    assert result == (1 if failed else 0)
    assert ("SETUP    Quiet / tests / Set up Python: median 25s" in output) is failed


def test_every_setup_exemption_carries_its_reason() -> None:
    config = json.loads((ROOT / ".github/ci-budget.json").read_text(encoding="utf-8"))
    rule = config["setup_step"]
    assert rule["budget_seconds"] == 10
    assert rule["prefixes"]
    for step, reason in rule["exempt"].items():
        assert len(reason.split()) >= 8, step
