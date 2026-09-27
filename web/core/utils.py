"""Utility functions for WCComps core functionality."""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import NamedTuple, TypedDict
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core.paginator import Page, Paginator
from django.db.models import Model, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from team.models import MAX_TEAMS, Team


def parse_datetime_to_utc(datetime_str: str, tz_name: str = "America/Los_Angeles") -> datetime:
    """Parse ISO 8601 datetime string and convert to UTC.

    Args:
        datetime_str: Datetime in format YYYY-MM-DDTHH:MM (ISO 8601 without seconds)
        tz_name: IANA timezone name (default: America/Los_Angeles)

    Returns:
        datetime object in UTC

    Raises:
        ValueError: If datetime_str is not in expected format
    """
    dt = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M")
    local_time = datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, tzinfo=ZoneInfo(tz_name))
    return local_time.astimezone(ZoneInfo("UTC"))


def ndjson_progress(step: str, current: int, total: int, ok: bool = True) -> str:
    """Encode a single progress line as newline-delimited JSON.

    Used by streaming views to report operation progress to the frontend.
    """
    import json

    return json.dumps({"step": step, "current": current, "total": total, "ok": ok}) + "\n"


class TeamGroupInfo(NamedTuple):
    """Result of get_team_from_groups()."""

    team: Team | None
    team_number: int | None
    is_team_account: bool


def get_team_from_groups(
    groups: list[str],
) -> TeamGroupInfo:
    """
    Extract team information from Authentik groups.

    Args:
        groups: List of Authentik group names

    Returns:
        TeamGroupInfo(team, team_number, is_team_account)
    """

    from core.permission_constants import extract_team_number

    for group in groups:
        team_number = extract_team_number(group)
        if team_number is not None and 1 <= team_number <= MAX_TEAMS:
            try:
                team = Team.objects.get(team_number=team_number)
                return TeamGroupInfo(team, team_number, True)
            except Team.DoesNotExist:
                pass

    return TeamGroupInfo(None, None, False)


class FilterSortPage[M: Model](TypedDict):
    """Result of filter_sort_paginate()."""

    page_obj: Page[M]
    current_sort: str


def filter_sort_paginate[M: Model](
    request: HttpRequest,
    queryset: QuerySet[M],
    *,
    valid_sort_fields: list[str],
    default_sort: str = "-created_at",
    page_size: int = 50,
) -> FilterSortPage[M]:
    """Validate sort field, apply ordering, and paginate a queryset.

    Reads ``sort`` and ``page`` from ``request.GET``.  The caller is
    responsible for applying all domain-specific filters to *queryset*
    before calling this helper.
    """
    sort_by = request.GET.get("sort", default_sort)

    if sort_by == "default":
        sort_by = ""

    if sort_by and sort_by not in valid_sort_fields:
        sort_by = default_sort

    if sort_by:
        queryset = queryset.order_by(sort_by)

    page_str = request.GET.get("page", "1")
    try:
        page_num = int(page_str)
    except TypeError, ValueError:
        page_num = 1

    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page_num)

    return FilterSortPage(page_obj=page_obj, current_sort=sort_by)


def bulk_approve(  # type: ignore[explicit-any]
    request: HttpRequest,
    *,
    field_name: str,
    queryset: QuerySet,  # type: ignore[type-arg]
    redirect_url: str,
    item_label: str,
    on_item: Callable[..., None],
) -> HttpResponse:
    """Extract IDs from POST, apply per-item approval, redirect with message.

    The caller provides a pre-filtered *queryset* and an *on_item* callback
    that mutates and saves each instance.
    """
    raw_ids = request.POST.getlist(field_name)

    if not raw_ids:
        messages.info(request, f"No {item_label}s selected for approval")
        return redirect(redirect_url)

    valid_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            valid_ids.append(int(raw_id))
        except ValueError, TypeError:
            continue

    if not valid_ids:
        messages.warning(request, f"No valid {item_label} IDs provided")
        return redirect(redirect_url)

    items = queryset.filter(id__in=valid_ids)
    count = 0
    for item in items:
        on_item(item)
        count += 1

    if count > 0:
        messages.success(request, f"Successfully approved {count} {item_label}(s)")
    else:
        messages.info(request, f"No unapproved {item_label}s found to approve")

    return redirect(redirect_url)


def role_sync_summary(stats: Mapping[str, object], *, dry_run: bool) -> str:
    """One-line role sync result, shared by the bot (ops channel) and the web Sync Roles page.

    Lives here, not in bot/, because the web container doesn't ship the bot package.
    """
    added = "would be added" if dry_run else "added"
    return (
        f"{'[DRY RUN] ' if dry_run else ''}Role sync complete: {stats.get('roles_added', 0)} {added}, "
        f"{stats.get('extra_linked', 0)} linked users with extra roles, "
        f"{stats.get('unlinked_holders', 0)} unlinked role holders, {stats.get('errors', 0)} errors "
        "(roles are never removed automatically)"
    )
