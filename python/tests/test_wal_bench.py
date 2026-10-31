"""The WAL harness measures both modes and survives a restart (S-11)."""
import os

import pytest

from jester import wal_bench


def test_recovery_holds_in_both_modes(tmp_path):
    path = tmp_path / "bench.db"
    wal_bench.seed(path, rows=200)
    for mode in wal_bench.MODES:
        got = wal_bench.recovery_check(path, mode)
        assert got["integrity"] == "ok", got
        assert got["row_present"] is True, got
        assert got["mode_persisted"] is True, got


def test_the_benchmark_runs_both_modes_without_lock_errors(tmp_path):
    path = tmp_path / "bench.db"
    wal_bench.seed(path, rows=2000)
    for mode in wal_bench.MODES:
        result = wal_bench.benchmark(path, mode, seconds=0.4, readers=1, batch=20)
        assert result["writes"] > 0, result
        assert result["reads"] > 0, result
        assert result["write_errors"] == 0, result
        assert result["read_errors"] == 0, result


def test_the_live_archive_is_refused_from_any_working_directory(tmp_path, monkeypatch):
    # The first guard compared against Path("data/jester.db"), which is
    # relative to wherever the command was run. From python/ with
    # `--db ../data/jester.db` that compared two different paths and let
    # seed() DELETE the real archive's nuggets. The guard must be anchored to
    # the repository, not the shell.
    monkeypatch.chdir(tmp_path)
    relative = os.path.relpath(wal_bench.LIVE_ARCHIVE, tmp_path)
    with pytest.raises(SystemExit):
        wal_bench.check_not_live(relative)
    wal_bench.check_not_live(tmp_path / "throwaway.db")  # no raise


def test_the_report_table_has_one_row_per_mode():
    table = wal_bench.format_markdown([
        {"mode": "delete", "writes": 1, "rows_written": 20, "write_errors": 0,
         "write_p95_ms": 1.0, "reads": 2, "read_errors": 0,
         "read_p50_ms": 0.5, "read_p95_ms": 0.9},
        {"mode": "wal", "writes": 3, "rows_written": 60, "write_errors": 0,
         "write_p95_ms": 2.0, "reads": 4, "read_errors": 0,
         "read_p50_ms": 0.4, "read_p95_ms": 0.8},
    ])
    assert "| delete |" in table and "| wal |" in table
    assert table.count("\n") == 4  # header, separator, two rows
