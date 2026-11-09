"""Local, transactional USD ledger. Amounts are integer cents, never floats."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata

MAX_CENTS = (1 << 63) - 1
APPLICATION_ID = 0x42554447
SCHEMA_VERSION = 1
CREATE_TABLE = """CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('expense', 'income')),
    amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents) = 'integer' AND amount_cents > 0),
    category TEXT NOT NULL,
    created_at TEXT NOT NULL
)"""


class LedgerError(Exception):
    """The ledger cannot safely satisfy an operation."""


def parse_amount(value):
    """Parse a positive ASCII decimal USD amount without rounding."""
    if not isinstance(value, str) or len(value) > 20 or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]{1,2})?", value
    ):
        raise ValueError("Use a positive USD amount with at most two decimal places, e.g. 25.50.")
    whole, _, fraction = value.partition(".")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if not 0 < cents <= MAX_CENTS:
        raise ValueError("Amount must be between 0.01 and 92233720368547758.07 USD.")
    return cents


def format_amount(cents):
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return "{}${}.{:02d}".format(sign, whole, fraction)


def clean_category(value):
    value = value.strip()
    if not value or len(value) > 100 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Category must contain 1 to 100 characters without control characters.")
    return value


def _validate_record(row):
    row_id, kind, cents, category, created = row
    if type(row_id) is not int or row_id <= 0 or kind not in ("income", "expense"):
        raise LedgerError("Invalid transaction data; the ledger was not changed.")
    if type(cents) is not int or not 0 < cents <= MAX_CENTS:
        raise LedgerError("Invalid stored amount; the ledger was not changed.")
    try:
        if not isinstance(category, str) or clean_category(category) != category:
            raise ValueError()
        if not isinstance(created, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00", created
        ):
            raise ValueError()
        datetime.fromisoformat(created)
    except (TypeError, ValueError):
        raise LedgerError("Invalid transaction data; the ledger was not changed.") from None


class Ledger:
    def __init__(self, path, timeout=5.0):
        self.path = Path(path).expanduser().absolute()
        self.timeout = timeout

    def _regular_file(self):
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(mode):
            raise LedgerError("Ledger path must be a regular file, not a directory or symbolic link.")
        return True

    def _validate(self, connection, initialize):
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        if initialize and (app_id, version, objects) == (0, 0, []):
            connection.execute(CREATE_TABLE)
            connection.execute("PRAGMA application_id = {}".format(APPLICATION_ID))
            connection.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))
        elif (app_id != APPLICATION_ID or version != SCHEMA_VERSION or objects != [
            ("table", "transactions", "transactions", CREATE_TABLE)
        ]):
            raise LedgerError("Unsupported ledger format or schema; the file was not changed.")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise LedgerError("Ledger integrity check failed; the file was not changed.")

    @contextmanager
    def _connect(self, write=False):
        connection = None
        try:
            exists = self._regular_file()
            if not exists and not write:
                yield None
                return
            if write and not exists:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass  # Another writer can have created the same ledger.
                else:
                    os.close(descriptor)
                self._regular_file()
            # URI quoting preserves spaces, #, ? and Unicode in user-selected paths.
            mode = "rw" if write else "ro"
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=" + mode, uri=True,
                timeout=self.timeout, isolation_level=None,
            )
            if write:
                connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._validate(connection, initialize=write)
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise LedgerError("Could not access the ledger: {}".format(error)) from error
        finally:
            if connection is not None:
                try:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
                except sqlite3.Error as error:
                    raise LedgerError(
                        "Could not finish closing the ledger; check 'budget list' with the same "
                        "--db path before retrying: {}".format(error)
                    ) from error

    @staticmethod
    def _records(connection):
        if connection is None:
            return []
        rows = connection.execute(
            "SELECT id, kind, amount_cents, category, created_at FROM transactions ORDER BY id"
        ).fetchall()
        for row in rows:
            _validate_record(row)
        return rows

    def records(self):
        with self._connect() as connection:
            return self._records(connection)

    def add(self, kind, cents, category):
        if kind not in ("expense", "income"):
            raise ValueError("Transaction kind must be expense or income.")
        if type(cents) is not int or not 0 < cents <= MAX_CENTS:
            raise ValueError("Amount must be a positive integer number of cents within the supported range.")
        category = clean_category(category)
        created = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connect(write=True) as connection:
            self._records(connection)  # Refuse to append to inconsistent existing data.
            cursor = connection.execute(
                "INSERT INTO transactions(kind, amount_cents, category, created_at) VALUES (?, ?, ?, ?)",
                (kind, cents, category, created),
            )
            transaction_id = cursor.lastrowid
        # Only acknowledge after COMMIT has succeeded.
        return transaction_id

    def summary(self):
        rows = self.records()
        grouped = {}
        income = expenses = 0
        for _, kind, cents, category, _ in rows:
            bucket = grouped.setdefault(category, [0, 0])
            if kind == "income":
                bucket[0] += cents
                income += cents
            else:
                bucket[1] += cents
                expenses += cents
        return grouped, income, expenses, len(rows)
