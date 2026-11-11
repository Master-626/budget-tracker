"""Independent persistence regressions added during repair review."""
import subprocess
import sys

import pytest

from budget_tracker.storage import MAX_CENTS, Ledger

from .test_budget import cli_process, process_env


@pytest.mark.parametrize("existing", [False, True])
def test_abrupt_exit_before_commit_never_records_expense(tmp_path, existing):
    db = tmp_path / "interrupted.sqlite3"
    if existing:
        Ledger(db).add("income", 100, "original")
    # Terminate inside Ledger.add's commit, bypassing Python finally/rollback.
    script = '''
import os, sqlite3, sys
from budget_tracker.storage import Ledger
original = sqlite3.connect
class InterruptedConnection(sqlite3.Connection):
    def commit(self):
        os._exit(77)
sqlite3.connect = lambda *args, **kwargs: original(*args, factory=InterruptedConnection, **kwargs)
Ledger(sys.argv[1]).add("expense", 99, "must-not-survive")
print("unexpected success")
'''
    process = subprocess.run([sys.executable, "-c", script, str(db)],
                             capture_output=True, text=True, env=process_env(), timeout=15)
    assert process.returncode == 77
    assert "unexpected success" not in process.stdout
    # A later writer recovers any journal and can initialize an interrupted new file.
    result = cli_process(db, "expense", "0.01", "-c", "after-recovery")
    assert result.returncode == 0, result.stderr
    rows = Ledger(db).records()
    assert [row[3] for row in rows] == (["original"] if existing else []) + ["after-recovery"]
    assert Ledger(db).summary()[2] == 1


def test_maximum_amount_persists_across_processes(tmp_path):
    db = tmp_path / "maximum.sqlite3"
    result = cli_process(db, "income", "92233720368547758.07")
    assert result.returncode == 0, result.stderr
    assert Ledger(db).records()[0][2] == MAX_CENTS
    result = cli_process(db, "summary")
    assert result.returncode == 0, result.stderr
    assert "Recorded income: $92233720368547758.07 USD" in result.stdout
    result = cli_process(db, "expense", "92233720368547758.08")
    assert result.returncode != 0 and "Added" not in result.stdout
    assert len(Ledger(db).records()) == 1


def test_close_failure_is_reported_without_false_success(tmp_path, monkeypatch):
    import sqlite3

    from .test_budget import run
    db = tmp_path / "close-failure.sqlite3"
    original_connect = sqlite3.connect

    class CloseFailure(sqlite3.Connection):
        def close(self):
            super().close()
            raise sqlite3.OperationalError("simulated close failure")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *args, **kwargs: original_connect(
            *args, factory=CloseFailure, **kwargs))
        result = run(db, "expense", "0.01")
    assert result.exit_code != 0
    assert "Added" not in result.output
    assert "Error:" in result.output and "simulated close failure" in result.output
    # The commit completed before close failed. A blind retry would duplicate it.
    assert len(Ledger(db).records()) == 1


def test_rollback_failure_still_closes_and_reports_error(tmp_path, monkeypatch):
    import sqlite3

    from .test_budget import run
    db = tmp_path / "rollback-failure.sqlite3"
    Ledger(db).add("income", 100, "original")
    original_connect = sqlite3.connect
    closed = []

    class RollbackFailure(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("simulated commit failure")

        def rollback(self):
            raise sqlite3.OperationalError("simulated rollback failure")

        def close(self):
            super().close()
            closed.append(True)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *args, **kwargs: original_connect(
            *args, factory=RollbackFailure, **kwargs))
        result = run(db, "expense", "0.01")
    assert result.exit_code != 0
    assert "Added" not in result.output
    assert "Error:" in result.output and "simulated rollback failure" in result.output
    assert closed == [True]
    assert len(Ledger(db).records()) == 1
