import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest
from click.testing import CliRunner

from budget_tracker import __version__
from budget_tracker.cli import main
from budget_tracker.storage import (
    APPLICATION_ID, MAX_CENTS, Ledger, LedgerError, clean_category, format_amount, parse_amount,
)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "ledger.sqlite3"


def run(db, *args, env=None):
    return CliRunner().invoke(main, ["--db", str(db)] + list(args), env=env)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("text,cents", [
    ("0.01", 1), ("1", 100), ("1.1", 110), ("25.50", 2550), ("0.10", 10),
    ("0001.02", 102), ("92233720368547758.07", MAX_CENTS),
])
def test_amounts_are_exact(text, cents):
    assert parse_amount(text) == cents
    assert parse_amount(format_amount(cents)[1:]) == cents


@pytest.mark.parametrize("text", [
    "0", "0.00", "-1", "+1", "NaN", "inf", "Infinity", "1e2", "1.001", "1.000", "1.",
    ".1", "1,000", "$1", " 1", "1 ", "1\n", "１２", "١", "", "9" * 10000,
    "92233720368547758.08", "92233720368547759", None, 1.1,
])
def test_bad_amounts_rejected(text):
    with pytest.raises(ValueError):
        parse_amount(text)


@pytest.mark.parametrize("category", ["", "  ", "x" * 101, "a\nb", "a\x1bb", "a\x7fb", "a\u202eb"])
def test_bad_categories(category):
    with pytest.raises(ValueError):
        clean_category(category)


def test_invalid_input_never_creates_ledger(db):
    for args in [("expense", "NaN"), ("expense", "1.001"), ("expense", "1", "-c", "\n")]:
        result = run(db, *args)
        assert result.exit_code != 0
        assert "Added" not in result.output
        assert not db.exists()


def test_empty_reads_do_not_create_files(db):
    db = db.parent / "absent" / "file.sqlite3"
    for command in ("summary", "list"):
        result = run(db, command)
        assert result.exit_code == 0, result.output
        assert "No recorded transactions" in result.output
    assert not db.parent.exists()


def test_exact_persistence_and_real_summary(db):
    for command, amount, category in [
        ("expense", "0.10", "food"), ("expense", "0.20", "food"), ("income", "1.00", "salary"),
    ]:
        result = run(db, command, amount, "-c", category)
        assert result.exit_code == 0, result.output
        assert "Added" in result.output
    rows = Ledger(db).records()
    assert len(rows) == 3 and [row[2] for row in rows] == [10, 20, 100]
    result = run(db, "summary")
    assert result.exit_code == 0, result.output
    assert "Recorded expenses: $0.30 USD" in result.output
    assert "Recorded income: $1.00 USD" in result.output
    assert "Recorded net: $0.70 USD" in result.output
    assert "$500" not in result.output and "Entertainment" not in result.output
    result = run(db, "list")
    assert "#1 | expense | $0.10 USD | food |" in result.output
    assert "+00:00" in result.output


def test_totals_do_not_overflow_sqlite_integer(db):
    ledger = Ledger(db)
    ledger.add("expense", MAX_CENTS, "large")
    ledger.add("expense", MAX_CENTS, "large")
    assert ledger.summary()[2] == MAX_CENTS * 2
    result = run(db, "summary")
    assert result.exit_code == 0, result.output
    assert "$184467440737095516.14" in result.output


def test_negative_net(db):
    Ledger(db).add("expense", 101, "other")
    assert "Recorded net: -$1.01 USD" in run(db, "summary").output


def test_category_text_is_literal_not_rich_markup_or_sql(db):
    category = "[red]O'Brien[/red]; DROP TABLE transactions; --"
    result = run(db, "expense", "1", "-c", category)
    assert result.exit_code == 0
    assert category in result.output
    assert Ledger(db).records()[0][3] == category
    result = run(db, "summary", env={"COLUMNS": "240"})
    assert category in result.output


def test_category_trim_and_case_preserved(db):
    ledger = Ledger(db)
    ledger.add("expense", 100, " Food ")
    ledger.add("expense", 100, "food")
    assert ledger.summary()[0] == {"Food": [0, 100], "food": [0, 100]}


def test_default_path_environment_and_explicit_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("BUDGET_TRACKER_DB", raising=False)
    assert CliRunner().invoke(main, ["--help"]).exit_code == 0
    assert CliRunner().invoke(main, ["--version"]).output.strip().endswith(__version__)
    from budget_tracker.cli import default_db
    default = Path(default_db())
    assert CliRunner().invoke(main, ["expense", "1"]).exit_code == 0
    assert Ledger(default).records()[0][2] == 100
    env_path = tmp_path / "env.sqlite3"
    monkeypatch.setenv("BUDGET_TRACKER_DB", str(env_path))
    assert CliRunner().invoke(main, ["income", "2"]).exit_code == 0
    assert Ledger(env_path).records()[0][2] == 200
    explicit = tmp_path / "explicit.sqlite3"
    assert run(explicit, "expense", "3").exit_code == 0
    assert Ledger(explicit).records()[0][2] == 300
    assert len(Ledger(env_path).records()) == 1


def test_path_with_uri_characters(tmp_path):
    db = tmp_path / "子 directory" / "a?b# c.sqlite3"
    assert run(db, "expense", "1").exit_code == 0
    assert Ledger(db).summary()[2] == 100


@pytest.mark.parametrize("contents", [b"not a database", b"SQLite format 3\x00" + b"\x00" * 100])
def test_corrupt_file_preserved(db, contents):
    db.write_bytes(contents)
    before = digest(db)
    for command in [("expense", "1"), ("summary",), ("list",)]:
        result = run(db, *command)
        assert result.exit_code != 0
        assert "Added" not in result.output
        assert "Traceback" not in result.output
        assert digest(db) == before


@pytest.mark.parametrize("mutation", [
    "PRAGMA application_id = 1",
    "PRAGMA user_version = 2",
    "CREATE TABLE unrelated(secret TEXT)",
    "CREATE TRIGGER ignore_insert BEFORE INSERT ON transactions BEGIN SELECT RAISE(IGNORE); END",
    "CREATE TRIGGER delete_insert AFTER INSERT ON transactions BEGIN DELETE FROM transactions; END",
    "CREATE TRIGGER alter_insert AFTER INSERT ON transactions BEGIN UPDATE transactions SET amount_cents = 1; END",
    "UPDATE transactions SET category = char(27)",
    "UPDATE transactions SET created_at = 'bad date'",
    "UPDATE transactions SET id = -1",
])
def test_schema_and_record_tampering_fail_closed(db, mutation):
    Ledger(db).add("expense", 100, "original")
    with sqlite3.connect(str(db)) as connection:
        connection.execute(mutation)
    before = digest(db)
    for args in [("expense", "2"), ("summary",), ("list",)]:
        result = run(db, *args)
        assert result.exit_code != 0, result.output
        assert "Added" not in result.output
        assert "Traceback" not in result.output
        assert digest(db) == before


def test_unrelated_sqlite_preserved(db):
    with sqlite3.connect(str(db)) as connection:
        connection.execute("CREATE TABLE important (data TEXT)")
        connection.execute("INSERT INTO important VALUES ('keep')")
    before = digest(db)
    assert run(db, "expense", "1").exit_code != 0
    assert digest(db) == before


def test_directory_and_non_directory_parent_fail(tmp_path):
    result = run(tmp_path, "expense", "1")
    assert result.exit_code != 0 and "Added" not in result.output
    blocker = tmp_path / "blocker"
    blocker.write_text("keep")
    result = run(blocker / "ledger.sqlite3", "expense", "1")
    assert result.exit_code != 0 and "Added" not in result.output
    assert blocker.read_text() == "keep"


def test_symlink_refused(db, tmp_path):
    target = tmp_path / "target.sqlite3"
    Ledger(target).add("expense", 100, "keep")
    try:
        db.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    before = digest(target)
    result = run(db, "expense", "1")
    assert result.exit_code != 0 and "Added" not in result.output
    assert digest(target) == before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_fifo_refused(db):
    os.mkfifo(db)
    result = run(db, "expense", "1")
    assert result.exit_code != 0 and "Added" not in result.output


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_new_file_is_owner_only(db):
    Ledger(db).add("expense", 100, "other")
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_write_lock_failure_preserves_records(db):
    ledger = Ledger(db, timeout=0.02)
    ledger.add("expense", 100, "first")
    connection = sqlite3.connect(str(db), isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(LedgerError, match="locked"):
            ledger.add("expense", 200, "blocked")
    finally:
        connection.rollback()
        connection.close()
    assert len(ledger.records()) == 1


def test_failed_commit_rolls_back_no_success(db, monkeypatch):
    Ledger(db).add("expense", 100, "original")
    reader = sqlite3.connect(str(db), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM transactions").fetchall()
    original_init = Ledger.__init__
    monkeypatch.setattr(Ledger, "__init__", lambda self, path: original_init(self, path, timeout=0.02))
    try:
        result = run(db, "expense", "2")
        assert result.exit_code != 0 and "Added" not in result.output
        assert "locked" in result.output
    finally:
        reader.rollback()
        reader.close()
    assert len(Ledger(db).records()) == 1


def test_simulated_disk_full_has_no_success(db, monkeypatch):
    Ledger(db).add("expense", 100, "original")
    original_connect = sqlite3.connect
    class FullConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("database or disk is full")
            return super().execute(sql, *args)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: original_connect(
        *args, factory=FullConnection, **kwargs))
    result = run(db, "expense", "2")
    assert result.exit_code != 0 and "Added" not in result.output
    assert "full" in result.output
    assert len(Ledger(db).records()) == 1


def process_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def cli_process(db, *args):
    return subprocess.run([sys.executable, "-m", "budget_tracker.cli", "--db", str(db)] + list(args),
                          capture_output=True, text=True, env=process_env(), timeout=15)


def test_fresh_process_persistence_and_exact_totals(db):
    for amount in ["0.10", "0.20"]:
        result = cli_process(db, "expense", amount, "-c", "food")
        assert result.returncode == 0, result.stderr
    result = cli_process(db, "summary")
    assert result.returncode == 0, result.stderr
    assert "Recorded expenses: $0.30 USD" in result.stdout
    result = cli_process(db, "list")
    assert result.returncode == 0 and "#2 | expense | $0.20" in result.stdout


def test_parallel_process_writes_no_lost_records(db):
    processes = [subprocess.Popen(
        [sys.executable, "-m", "budget_tracker.cli", "--db", str(db), "expense", "0.01"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=process_env(),
    ) for _ in range(8)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, (stdout, stderr)
    rows = Ledger(db).records()
    assert len(rows) == 8 and len({row[0] for row in rows}) == 8
    assert Ledger(db).summary()[2] == 8


def test_schema_constraints(db):
    Ledger(db).add("expense", 100, "food")
    with sqlite3.connect(str(db)) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        for bad in [0, -1, 1.25]:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE transactions SET amount_cents=?", (bad,))
