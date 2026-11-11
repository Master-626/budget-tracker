"""Command line interface for an exact, local USD ledger."""
from functools import wraps
from pathlib import Path

import click
from rich.cells import cell_len
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
    if value is None:
        return None
    try:
        return clean_category(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from None


def summary_console(title, categories, file=None, force_terminal=None):
    """Build a console wide enough to show every category and amount.

    On a real terminal, Rich ignores an explicit width unless height is set,
    then ellipsizes cells to the detected terminal width. Distinct long
    category names would otherwise look identical.
    """
    longest_name = max(cell_len(name) for name in categories)
    longest_amount = 0
    for incoming, outgoing in categories.values():
        longest_amount = max(
            longest_amount, len(format_amount(incoming)), len(format_amount(outgoing))
        )
    name_width = max(cell_len("Category"), longest_name)
    amount_width = max(cell_len("Income"), cell_len("Expenses"), longest_amount)
    # Padding inside each column, plus the four box-drawing edges.
    width = max(80, cell_len(title) + 4, name_width + amount_width * 2 + 24)
    height = len(categories) + 16
    return Console(
        file=file, width=width, height=height, soft_wrap=True, force_terminal=force_terminal,
    )


def print_category_table(categories, file=None, force_terminal=None):
    title = "Recorded transactions (USD, all time)"
    table = Table(title=title)
    table.add_column("Category", no_wrap=True, overflow="ignore")
    table.add_column("Income", justify="right", no_wrap=True, overflow="ignore")
    table.add_column("Expenses", justify="right", no_wrap=True, overflow="ignore")
    for name, (incoming, outgoing) in sorted(categories.items()):
        table.add_row(Text(name), format_amount(incoming), format_amount(outgoing))
    summary_console(title, categories, file=file, force_terminal=force_terminal).print(table)


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
@click.option("--category", "-c", callback=category_option,
              help="Limit totals to this case-sensitive category.")
@click.pass_obj
@ledger_errors
def summary(ledger, category):
    """Show all-time totals from recorded transactions, grouped by category."""
    categories, total_income, total_expenses, count = ledger.summary(category)
    if categories:
        if category is not None:
            click.echo("Category filter: {}".format(category))
        print_category_table(categories)
    elif category is None:
        click.echo("No recorded transactions.")
    else:
        click.echo("No recorded transactions for category {}.".format(category))
    click.echo("Transactions: {}".format(count))
    click.echo("Recorded income: {} USD".format(format_amount(total_income)))
    click.echo("Recorded expenses: {} USD".format(format_amount(total_expenses)))
    click.echo("Recorded net: {} USD (income minus expenses; not a bank balance)".format(
        format_amount(total_income - total_expenses)))


@main.command(name="list")
@click.option("--category", "-c", callback=category_option,
              help="Limit the list to this case-sensitive category.")
@click.pass_obj
@ledger_errors
def list_transactions(ledger, category):
    """List recorded transactions with their IDs and UTC recording times."""
    rows = ledger.records(category)
    if not rows:
        if category is None:
            click.echo("No recorded transactions.")
        else:
            click.echo("No recorded transactions for category {}.".format(category))
        return
    if category is not None:
        click.echo("Category filter: {}".format(category))
    # Plain lines keep IDs and exact amounts readable even in narrow terminals.
    for row_id, kind, cents, name, created in rows:
        click.echo("#{} | {} | {} USD | {} | {}".format(
            row_id, kind, format_amount(cents), name, created))


if __name__ == "__main__":
    main()
