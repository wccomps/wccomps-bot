"""Heartbeat files written by the bot's queue, dashboard and timer loops, read by `python -m bot.health`.

A loop whose database connection dies keeps running and logging while doing nothing, and the
process still looks alive. Each loop records a heartbeat after a *successful* pass; the health
check (a k8s liveness probe) fails when one goes stale, so the pod gets restarted.

Deliberately free of Django imports so the probe can never hang on the database it observes.
"""

import logging
import os
import time
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_DIR = "/app/run/heartbeat"

# Allowed age per loop, several missed passes each: queue polls every 2s, dashboard every 10s,
# competition timer every 60s.
BUDGET_SECONDS: dict[str, int] = {"queue": 60, "dashboard": 120, "timer": 300}


class _ClosableBot(Protocol):
    def is_closed(self) -> bool: ...


def heartbeat_dir() -> Path:
    return Path(os.environ.get("BOT_HEARTBEAT_DIR", DEFAULT_DIR))


def record(name: str, bot: _ClosableBot) -> None:
    """Record a successful loop pass. Never raises; a failed write must not break the loop.

    Gated on the client not being closed. Not on is_ready(): that flag isn't cleared by a
    transient websocket drop, so it says nothing useful about gateway health.
    """
    if bot.is_closed():
        return
    try:
        directory = heartbeat_dir()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{name}.tmp"
        tmp.write_text(str(time.time()))
        tmp.replace(directory / name)
    except OSError as e:
        logger.debug(f"Could not write {name} heartbeat: {e}")
