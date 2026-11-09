"""Command line interface for an exact, local USD ledger."""
from functools import wraps
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .storage import Ledger, LedgerError, clean_category, format_amount, parse_amount


def default_db():
    return str(Path(click.get_app_dir("budget-tracker")) / "ledger.sqlite3")


def amount_option(ctx, param, value):
    try:
        return parse_amount(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from None


def category_option(ctx, param, value):
    try:
        return clean_category(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from None


def ledger_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LedgerError as error:
            raise click.ClickException(str(error)) from None
    return wrapped


@click.group()
@click.version_option(version=__version__)
@click.option("--db", envvar="BUDGET_TRACKER_DB", default=default_db,
              type=click.Path(dir_okay=False), show_default="per-user application data/ledger.sqlite3",
              help="Local USD ledger file. Supply this option before the command.")
@click.pass_context
def main(ctx, db):
    """Track recorded USD income and expenses locally."""
    ctx.obj = Ledger(db)


def record_options(function):
    function = click.option("--category", "-c", default="other", show_default=True,
                            callback=category_option, help="Case-sensitive transaction category.")(function)
    function = click.argument("amount", callback=amount_option)(function)
    return function


@main.command()
@record_options
@click.pass_obj
@ledger_errors
def expense(ledger, amount, category):
    """Record a positive USD expense (for example, 25.50)."""
    transaction_id = ledger.add("expense", amount, category)
    click.echo("Added expense #{}: {} USD ({})".format(transaction_id, format_amount(amount), category))


@main.command()
@record_options
@click.pass_obj
@ledger_errors
def income(ledger, amount, category):
    """Record positive USD income."""
    transaction_id = ledger.add("income", amount, category)
    click.echo("Added income #{}: {} USD ({})".format(transaction_id, format_amount(amount), category))


@main.command()
@click.pass_obj
@ledger_errors
def summary(ledger):
    """Show all-time totals from recorded transactions, grouped by category."""
    categories, total_income, total_expenses, count = ledger.summary()
    if categories:
        table = Table(title="Recorded transactions (USD, all time)")
        table.add_column("Category")
        table.add_column("Income", justify="right")
        table.add_column("Expenses", justify="right")
        for category, (incoming, outgoing) in sorted(categories.items()):
            table.add_row(Text(category), format_amount(incoming), format_amount(outgoing))
        Console().print(table)
    else:
        click.echo("No recorded transactions.")
    click.echo("Transactions: {}".format(count))
    click.echo("Recorded income: {} USD".format(format_amount(total_income)))
    click.echo("Recorded expenses: {} USD".format(format_amount(total_expenses)))
    click.echo("Recorded net: {} USD (income minus expenses; not a bank balance)".format(
        format_amount(total_income - total_expenses)))


@main.command(name="list")
@click.pass_obj
@ledger_errors
def list_transactions(ledger):
    """List recorded transactions with their IDs and UTC recording times."""
    rows = ledger.records()
    if not rows:
        click.echo("No recorded transactions.")
        return
    # Plain lines keep IDs and exact amounts readable even in narrow terminals.
    for row_id, kind, cents, category, created in rows:
        click.echo("#{} | {} | {} USD | {} | {}".format(
            row_id, kind, format_amount(cents), category, created))


if __name__ == "__main__":
    main()
