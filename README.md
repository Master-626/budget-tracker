# Budget Tracker

A small, local command-line ledger for recorded **USD** income and expenses.
Python 3.8 or newer. Data is stored in SQLite on your computer; no account,
network connection, or server is needed.

## What works

- Record positive expenses and income with a category.
- List saved transactions, their IDs, and UTC recording times.
- Show all-time income, expense, and net totals, including totals by category.
- Preserve records across command invocations using SQLite transactions.
- Calculate with integer cents, without floating-point rounding.

This is a manual ledger, not a bank connection, accounting system, or financial
adviser. “Recorded net” means recorded income minus recorded expenses, **not**
your bank balance. This version does not provide budgets, alerts, period reports,
charts, multiple accounts/currencies, import/export, savings goals, or transaction
editing/deletion. Earlier README feature claims for those features were premature.

## Install

```bash
git clone https://github.com/Master-626/budget-tracker.git
cd budget-tracker
python -m pip install .
budget --help
```

## Use

```bash
budget expense 25.50 -c food
budget expense 3.75 -c transport
budget income 1000 -c salary
budget list
budget summary
```

An empty ledger displays zero recorded totals and no example transactions. Read
commands do not create a missing database. Categories default to `other`; leading
and trailing whitespace is trimmed. Category names are case-sensitive, contain
at most 100 characters, and must not contain control characters.

Amounts must be positive USD decimals with no more than two fractional digits,
for example `12`, `12.3`, or `12.34`. Zero, negative values, scientific notation,
commas, currency symbols, non-finite values, and extra decimal places are rejected
rather than silently rounded. One transaction can contain at most
`92233720368547758.07` USD. Totals use Python integers and can exceed that amount.
All transactions in a database are interpreted as USD; do not mix currencies.

## Where data lives

By default, `ledger.sqlite3` is stored under Click's per-user application data
folder:

- Linux: `$XDG_CONFIG_HOME/budget-tracker/`, or `~/.config/budget-tracker/`.
- macOS: `~/Library/Application Support/budget-tracker/`.
- Windows: `%APPDATA%\budget-tracker\`.

Choose another local database with a global option **before** the command:

```bash
budget --db ./household.sqlite3 expense 25.50 -c food
budget --db ./household.sqlite3 summary
```

You can also set `BUDGET_TRACKER_DB`. `--db` takes precedence over that environment
variable. Relative paths are relative to the current working directory; use an
absolute path in the environment variable to keep the same ledger across folders.

## Data safety and limits

Writes use SQLite transactions with full synchronous commits. Success is printed
only after commit. Concurrent writers wait up to five seconds, then return a
nonzero exit code if the database remains locked. Invalid inputs are rejected
before writing. Corrupt files, unsupported database versions/schemas, unexpected
triggers, invalid stored records, directory paths, and final-component symlinks
are rejected instead of being silently replaced or repaired. An empty SQLite
file can be initialized as a new ledger.

Use a trusted, local directory and filesystem. Concurrent file replacement,
untrusted directory access, network/synchronized filesystem locking, disk failure,
and power-loss guarantees are outside this tool's control. A newly created file
is owner-readable/writable on POSIX systems; existing file permissions are not
changed. Records are **not encrypted**. Protect the file and backups with your OS
account permissions and disk encryption where appropriate.

A failed operation does not print success. If the process is interrupted around
commit or a system error leaves the outcome uncertain, check `budget list` before
retrying: identical commands are separate transactions and are not deduplicated.
An unsuccessful first write may leave an empty database file. Do not delete or
manually repair a nonempty database merely because a command fails.

To back up the ledger, stop all commands using it and copy the database to a safe
location, or use SQLite's supported backup tools. Do not copy an active database
file by itself. Keep backups before upgrading. Never commit financial records to
Git or attach them to a public bug report.

Version 0.1.0 printed a success message but did not save transactions. Version
0.2.0 cannot recover those previously unsaved entries; re-enter them only from
records you trust.

## Development and verification

```bash
python -m pip install '.[dev]' build
python -m pytest
python -m build
# In a separate virtual environment, install the generated wheel:
python -m pip install dist/budget_tracker-0.2.0-py3-none-any.whl
python scripts/wheel_smoke.py
```

Tests cover independent-process persistence, concurrent writers, exact decimal
amounts and large totals, validation, literal category text, schema/corruption
rejection, write locks, a failed commit, and simulated disk-full errors. CI is
configured for Python 3.8, 3.12, and 3.14 on Linux, and Python 3.12 on macOS and
Windows. A configured job is not evidence that it has run successfully.

## License

MIT. See [LICENSE](LICENSE).
