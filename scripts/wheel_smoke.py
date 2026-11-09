"""Smoke-test the installed wheel and entry point outside the source directory."""
import os
from pathlib import Path
import subprocess
import sysconfig
import tempfile


def main():
    executable = Path(sysconfig.get_path("scripts")) / ("budget.exe" if os.name == "nt" else "budget")
    if not executable.is_file():
        raise RuntimeError("Installed budget entry point not found: {}".format(executable))
    with tempfile.TemporaryDirectory(prefix="budget-wheel-") as temporary:
        directory = Path(temporary)
        db = directory / "test.sqlite3"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("BUDGET_TRACKER_DB", None)
        def run(*arguments):
            result = subprocess.run([str(executable), "--db", str(db)] + list(arguments),
                                    cwd=str(directory), env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout
        assert "0.2.0" in run("--version")
        assert "No recorded transactions" in run("summary")
        assert not db.exists()
        assert "Added expense #1" in run("expense", "0.10", "-c", "food")
        assert "Added expense #2" in run("expense", "0.20", "-c", "food")
        assert "Added income #3" in run("income", "1", "-c", "salary")
        assert "#2 | expense | $0.20 USD | food" in run("list")
        output = run("summary")
        assert "Recorded expenses: $0.30 USD" in output
        assert "Recorded income: $1.00 USD" in output
        assert "Recorded net: $0.70 USD" in output
        print("Installed-wheel CLI smoke passed: persistence, exact amounts, income, list, summary.")


if __name__ == "__main__":
    main()
