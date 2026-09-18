"""CLI interface."""
import click
from rich.console import Console
from rich.table import Table

console = Console()

@click.group()
@click.version_option(version="0.1.0")
def main():
    """Budget Tracker - Track your income and expenses."""
    pass

@main.command()
@click.argument("amount", type=float)
@click.option("--category", "-c", help="Expense category")
def expense(amount, category):
    """Add an expense."""
    console.print(f"✅ Added expense: ${amount} ({category or 'other'})")

@main.command()
def summary():
    """Show budget summary."""
    table = Table(title="Budget Summary")
    table.add_column("Category", style="cyan")
    table.add_column("Amount", style="green")
    table.add_row("Food", "$500")
    table.add_row("Transport", "$200")
    table.add_row("Entertainment", "$150")
    console.print(table)

if __name__ == "__main__":
    main()
