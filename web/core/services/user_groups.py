"""Keep stored Authentik groups in step with Authentik between logins."""

import logging
from dataclasses import dataclass
from typing import NamedTuple, cast

from django.db import transaction

from core.authentik_manager import AuthentikManager
from core.models import UserGroups

logger = logging.getLogger(__name__)


class GroupRefreshAbortedError(Exception):
    """Authentik's answer looks wrong; stored groups were left alone."""


@dataclass(frozen=True)
class GroupRefreshResult:
    checked: int
    changed: int


class _Group(NamedTuple):
    name: str
    parents: list[str]


class _User(NamedTuple):
    is_active: bool
    groups: list[str]


def _expand(direct: list[str], groups: dict[str, _Group]) -> list[str]:
    """Direct group pks plus all their ancestors, as sorted names (what the OIDC groups claim holds)."""
    seen: set[str] = set()
    stack = list(direct)
    while stack:
        pk = stack.pop()
        if pk in seen or pk not in groups:
            continue
        seen.add(pk)
        stack.extend(groups[pk].parents)
    return sorted(groups[pk].name for pk in seen)


def refresh_user_groups(manager: AuthentikManager | None = None) -> GroupRefreshResult:
    """Re-read every stored user's groups from Authentik.

    Logins only refresh the logging-in user, so removals, deactivations and deletions in
    Authentik never reached permission checks (web or Discord). Inactive or deleted users
    get no groups. Raises GroupRefreshAbortedError, changing nothing, if Authentik's answer
    doesn't recognise most users who currently hold groups.
    """
    manager = manager or AuthentikManager()
    groups = {
        str(g["pk"]): _Group(str(g["name"]), [str(p) for p in cast(list[object], g.get("parents") or [])])
        for g in manager.list_all_groups()
    }
    users = {
        str(u["uid"]): _User(bool(u["is_active"]), [str(pk) for pk in cast(list[object], u.get("groups") or [])])
        for u in manager.list_all_users()
    }
    if not users:
        raise GroupRefreshAbortedError("Authentik returned no users")

    rows = list(UserGroups.objects.select_related("user"))
    holding = [row for row in rows if row.groups]
    recognised = sum(row.authentik_id in users for row in holding)
    if holding and recognised * 2 < len(holding):
        raise GroupRefreshAbortedError(f"only {recognised} of {len(holding)} users with groups found in Authentik")

    changed = 0
    with transaction.atomic():
        for row in rows:
            user = users.get(row.authentik_id)
            current = _expand(user.groups, groups) if user and user.is_active else []
            if current == sorted(row.groups):
                continue
            removed = sorted(set(row.groups) - set(current))
            added = sorted(set(current) - set(row.groups))
            reason = "deleted in Authentik" if not user else "inactive" if not user.is_active else "changed"
            logger.info(f"Groups for {row.user.username} {reason}: removed {removed}, added {added}")
            row.groups = current
            row.save(update_fields=["groups"])
            changed += 1

    return GroupRefreshResult(checked=len(rows), changed=changed)
