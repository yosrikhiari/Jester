"""V3-11/V3-24: the CI gates that make dependency drift fail a build.

These are file-shape checks, not a CI run. They pin the steps that must exist
and stay blocking: `govulncheck` on Go, `npm audit --audit-level=high` on the
frontend, and the requirements/lock parity check on both Python jobs.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"


def _workflow():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _job_text(job):
    return "\n".join(
        str(step.get("run", "")) + "\n" + str(step.get("uses", ""))
        for step in job.get("steps", [])
    )


def test_ci_yaml_parses_and_keeps_every_existing_job():
    ci = _workflow()
    assert set(ci["jobs"]) >= {"python", "windows", "go", "frontend", "sonar"}


def test_govulncheck_is_a_blocking_go_step():
    go = _workflow()["jobs"]["go"]
    steps = [s for s in go["steps"] if "govulncheck" in str(s.get("run", ""))]
    assert steps, "the Go job has no govulncheck step"
    assert all(not s.get("continue-on-error") for s in steps), (
        "govulncheck must block the build, not report and continue")


def test_npm_audit_high_is_a_blocking_frontend_step():
    fe = _workflow()["jobs"]["frontend"]
    steps = [s for s in fe["steps"]
             if "npm audit --audit-level=high" in str(s.get("run", ""))]
    assert steps, "the frontend job has no blocking npm audit"
    assert all(not s.get("continue-on-error") for s in steps)


def test_pip_audit_stays_advisory():
    python = _workflow()["jobs"]["python"]
    audit = [s for s in python["steps"] if "pip_audit" in str(s.get("run", ""))]
    assert audit and all(s.get("continue-on-error") for s in audit), (
        "pip-audit had no clean baseline at HEAD; it must stay advisory")


def test_both_python_jobs_run_the_lock_parity_check():
    for name in ("python", "windows"):
        text = _job_text(_workflow()["jobs"][name])
        assert "scripts/check_lock_parity.py" in text, f"{name} job is missing parity"


def test_dependabot_tracks_go_modules():
    data = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    ecosystems = {(u["package-ecosystem"], u["directory"]) for u in data["updates"]}
    assert ("gomod", "/go") in ecosystems
