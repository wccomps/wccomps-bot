"""Bot liveness check: `python -m bot.health` exits 0 if every heartbeat in BUDGET_SECONDS is fresh, else 1.

Intended as a Kubernetes exec liveness probe for the bot pod (it has no HTTP listener).
Imports nothing from Django, so it can't hang on the database.
"""

import sys
import time

from bot.heartbeat import BUDGET_SECONDS, heartbeat_dir


def check() -> list[str]:
    """Problems found, one per stale or missing loop heartbeat. Empty means healthy."""
    problems = []
    now = time.time()
    for name, budget in BUDGET_SECONDS.items():
        path = heartbeat_dir() / name
        try:
            age = now - float(path.read_text())
        except OSError, ValueError:
            problems.append(f"{name}: heartbeat missing ({path})")
            continue
        if age > budget:
            problems.append(f"{name}: heartbeat stale ({age:.0f}s old, budget {budget}s)")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if not problems:
        print("ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
