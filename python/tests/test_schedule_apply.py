"""config/schedule.yaml is the declaration the machine must match (S-20).

`install` registers one task at a time, so before this a task switched off in
Task Scheduler (the generative loop, paused for quota since 2026-10-02) was
indistinguishable from one that had never existed, and the pause lived on one
machine only. The declaration is the repo's intent; `schedule apply` makes the
machine match it; Doctor reports when it does not.

Every test runs against an in-memory Task Scheduler. Nothing here touches this
machine's real one.
"""
import subprocess

import pytest

from jester import cli, schedule

EIGHT = [
    "JesterNightly",
    "JesterNightlyScrape",
    "JesterNightlyTreat",
    "JesterNightlySignals",
    "JesterNightlySignalsBrief",
    "JesterNightlySignalsHiring",
    "JesterNightlySignalsSweep",
    "JesterNightlyLeadsReddit",
]


class FakeSchtasks:
    """An in-memory Task Scheduler behind `schedule._run`.

    `create_fails` names tasks whose /create returns non-zero, so the failure
    path is exercised without a real scheduler.
    """

    def __init__(self, create_fails=()):
        self.tasks = {}
        self.calls = []
        self.create_fails = set(create_fails)

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "/create":
            task = cmd[cmd.index("/tn") + 1]
            if task in self.create_fails:
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Access is denied.")
            self.tasks[task] = {
                "sc": cmd[cmd.index("/sc") + 1] if "/sc" in cmd else "DAILY",
                "mo": cmd[cmd.index("/mo") + 1] if "/mo" in cmd else "",
                "st": cmd[cmd.index("/st") + 1] if "/st" in cmd else "",
                "day": cmd[cmd.index("/d") + 1] if "/d" in cmd else "",
                "enabled": True,
            }
            return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")
        if verb == "/query":
            task = cmd[cmd.index("/tn") + 1]
            if task not in self.tasks:
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: cannot find")
            return subprocess.CompletedProcess(cmd, 0, self._verbose(task), "")
        if verb == "/change":
            task = cmd[cmd.index("/tn") + 1]
            if task not in self.tasks:
                return subprocess.CompletedProcess(cmd, 1, "", "ERROR: cannot find")
            self.tasks[task]["enabled"] = "/enable" in cmd
            return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")
        if verb == "/delete":
            self.tasks.pop(cmd[cmd.index("/tn") + 1], None)
            return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")
        return subprocess.CompletedProcess(cmd, 0, "SUCCESS", "")

    def _verbose(self, task):
        t = self.tasks[task]
        start = t["st"]
        if start:
            hour, minute = (int(x) for x in start.split(":"))
            start = (f"{hour % 12 or 12}:{minute:02d}:00 "
                     f"{'AM' if hour < 12 else 'PM'}")
        if t["sc"] == "DAILY":
            kind, repeat = "Daily", "Disabled"
        elif t["sc"] == "WEEKLY":
            kind, repeat = "Weekly", "Disabled"
        elif t["sc"] == "HOURLY":
            kind, repeat = "One Time Only, Hourly", f"{t['mo']} Hour(s), 0 Minute(s)"
        else:
            kind, repeat = "One Time Only, Minute", f"0 Hour(s), {t['mo']} Minute(s)"
        return (
            f"TaskName:                             \\{task}\n"
            "Next Run Time:                        N/A\n"
            "Status:                               Ready\n"
            "Last Run Time:                        N/A\n"
            "Last Result:                          0\n"
            "Scheduled Task State:                 "
            f"{'Enabled' if t['enabled'] else 'Disabled'}\n"
            f"Schedule Type:                        {kind}\n"
            f"Start Time:                           {start}\n"
            f"Days:                                 {t['day']}\n"
            f"Repeat: Every:                        {repeat}\n"
        )

    def created(self):
        return [c for c in self.calls if "/create" in c]

    def changes(self):
        return [c for c in self.calls if "/change" in c]


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeSchtasks()
    monkeypatch.setattr(schedule, "_run", fake)
    return fake


def test_the_shipped_declaration_names_the_eight_tasks():
    specs = schedule.load_desired()
    assert [s["task"] for s in specs] == EIGHT
    assert {s["task"] for s in specs if not s["enabled"]} == {
        "JesterNightly", "JesterNightlyScrape", "JesterNightlyTreat"}
    cadence = {s["task"]: schedule.describe_cadence(s) for s in specs}
    assert cadence["JesterNightly"] == "every 30 minutes"
    assert cadence["JesterNightlyScrape"] == "every 1 hour"
    assert cadence["JesterNightlySignalsSweep"] == "daily at 08:00"
    assert cadence["JesterNightlySignalsHiring"] == "daily at 09:30"


def test_apply_installs_every_declared_task_and_a_second_apply_changes_nothing(host):
    first = schedule.apply_desired()
    assert first["ok"] is True, first
    installed = {r["task"] for r in first["results"] if r["action"] == "installed"}
    assert installed == set(EIGHT)
    assert len(host.created()) == 8
    assert {t for t, v in host.tasks.items() if not v["enabled"]} == {
        "JesterNightly", "JesterNightlyScrape", "JesterNightlyTreat"}

    second = schedule.apply_desired()
    assert second["ok"] is True
    assert [r["action"] for r in second["results"]] == ["unchanged"] * 8
    assert len(host.created()) == 8, "a second apply must not re-register anything"
    assert len(host.changes()) == 3, "nor flip the three paused tasks again"


def test_a_dry_run_changes_nothing_and_says_what_would(host):
    res = schedule.apply_desired(dry_run=True)
    assert res["ok"] is True
    assert all(r["action"].startswith("would ") for r in res["results"])
    assert host.created() == []
    assert host.tasks == {}


def test_the_cli_prints_one_line_per_task_and_a_total(host, capsys):
    cli.main(["schedule", "apply", "--dry-run"])
    out = capsys.readouterr().out
    assert "JesterNightly" in out and "would install" in out
    assert "8 task(s) would change" in out


def test_a_declaration_can_say_weekly_and_scope_one_tick(tmp_path, host):
    path = tmp_path / "week.yaml"
    path.write_text(
        "tasks:\n"
        "  - task: JesterNightlySignals\n"
        "    command: signals\n"
        "    cadence: {weekly: MON, at: '09:00'}\n"
        "    options: {only: hackernews, max_posts: 5}\n",
        encoding="utf-8")
    assert schedule.apply_desired(path)["ok"] is True
    create = host.created()[-1]
    assert create[create.index("/sc") + 1] == "WEEKLY"
    assert create[create.index("/d") + 1] == "MON"
    assert schedule.read_launcher("signals")["extra"] == [
        "--only", "hackernews", "--max-posts", "5"]
    again = schedule.apply_desired(path)
    assert [r["action"] for r in again["results"]] == ["unchanged"]


def test_changing_the_declared_scope_reinstalls_the_launcher(host, tmp_path):
    narrow = tmp_path / "narrow.yaml"
    narrow.write_text(
        "tasks:\n"
        "  - task: JesterNightlySignals\n"
        "    command: signals\n"
        "    cadence: {at: '09:00'}\n"
        "    options: {only: hackernews, max_posts: 5}\n", encoding="utf-8")
    wide = tmp_path / "wide.yaml"
    wide.write_text(
        "tasks:\n"
        "  - task: JesterNightlySignals\n"
        "    command: signals\n"
        "    cadence: {at: '09:00'}\n"
        "    options: {only: reddit}\n", encoding="utf-8")

    assert schedule.apply_desired(narrow)["ok"] is True
    assert schedule.read_launcher("signals")["extra"] == [
        "--only", "hackernews", "--max-posts", "5"]

    changed = schedule.apply_desired(wide)
    assert [r["action"] for r in changed["results"]] == ["installed"]
    assert schedule.read_launcher("signals")["extra"] == ["--only", "reddit"]

    assert [r["action"] for r in schedule.apply_desired(wide)["results"]] == [
        "unchanged"], "the scope already matches; nothing to re-register"


def test_doctor_names_a_launcher_whose_scope_was_edited_by_hand(host):
    assert schedule.apply_desired()["ok"] is True
    path = schedule.launcher_path("cycle")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("-m jester.cli cycle", "-m jester.cli cycle --only hackernews"),
        encoding="utf-8")

    notes = cli.schedule_drift_notes()

    assert any(n.startswith("JesterNightly:") and "scrape options" in n
               and "hackernews" in n for n in notes), notes


def test_apply_restores_a_deleted_launcher(host):
    assert schedule.apply_desired()["ok"] is True
    schedule.launcher_path("cycle").unlink()

    notes = cli.schedule_drift_notes()
    assert any("launcher missing" in n for n in notes), notes

    assert schedule.apply_desired()["ok"] is True
    assert schedule.launcher_path("cycle").is_file()


@pytest.mark.parametrize("body", [
    "tasks: []\n",
    "tasks:\n  - task: T\n    command: nonsense\n    cadence: {every: 30}\n",
    "tasks:\n  - task: T\n    command: cycle\n    cadence: {every: 30, at: '09:00'}\n",
    "tasks:\n  - task: T\n    command: cycle\n    cadence: {every: 0}\n",
    "tasks:\n  - task: T\n    command: cycle\n    cadence: {at: 9am}\n",
    "tasks:\n  - task: T\n    command: cycle\n    cadence: {every: 30}\n"
    "  - task: T\n    command: ingest\n    cadence: {every: 30}\n",
    "tasks:\n  - task: T\n    command: cycle\n    cadence: {every: 30}\n"
    "    options: {depth: 3}\n",
])
def test_a_bad_declaration_is_refused_before_anything_installs(tmp_path, host, body):
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(schedule.ScheduleError):
        schedule.apply_desired(path)
    assert host.created() == []


def test_the_cli_exits_2_on_a_missing_file(host, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["schedule", "apply", "--file", str(tmp_path / "nope.yaml")])
    assert exc.value.code == 2
    assert "no schedule file" in capsys.readouterr().out


def test_apply_names_the_task_it_could_not_create(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    fake = FakeSchtasks(create_fails={"JesterNightlySignalsBrief"})
    monkeypatch.setattr(schedule, "_run", fake)
    res = schedule.apply_desired()
    assert res["ok"] is False
    failed = [r for r in res["results"] if r["action"] == "failed"]
    assert [r["task"] for r in failed] == ["JesterNightlySignalsBrief"]
    assert "Access is denied" in failed[0]["detail"]


def test_apply_refuses_on_a_host_without_task_scheduler(monkeypatch):
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    res = schedule.apply_desired()
    assert res["ok"] is False
    assert "cron" in res["detail"]


def test_doctor_names_the_tasks_that_no_longer_match(host):
    assert schedule.apply_desired()["ok"] is True
    host.tasks.pop("JesterNightlySignalsSweep")
    host.tasks["JesterNightlySignalsHiring"]["st"] = "11:30"

    notes = cli.schedule_drift_notes()

    assert len(notes) == 2
    assert any(n.startswith("JesterNightlySignalsSweep: not installed")
               and "daily at 08:00" in n for n in notes)
    assert any(n.startswith("JesterNightlySignalsHiring:")
               and "11:30:00 AM" in n and "daily at 09:30" in n for n in notes)


def test_a_paused_task_absent_from_the_machine_is_still_paused(host):
    """A box that never installed the paused tasks agrees with the declaration:
    off is off whether the task is disabled or does not exist."""
    assert schedule.apply_desired()["ok"] is True
    for task in ("JesterNightly", "JesterNightlyScrape", "JesterNightlyTreat"):
        host.tasks.pop(task)
    assert schedule.desired_drift() == []
    assert cli.schedule_drift_notes() == []
