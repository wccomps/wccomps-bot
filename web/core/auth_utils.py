"""
Simplified Authentik-only authorization utilities.
"""

from collections.abc import Callable
from typing import Concatenate, ParamSpec

from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpRequest, HttpResponseBase

from .models import UserGroups
from .permission_constants import PERMISSION_MAP as PERMISSION_MAP
from .permission_constants import check_groups_for_permission as check_groups_for_permission
from .permission_constants import extract_team_number as extract_team_number

P = ParamSpec("P")
type ViewFunc[**P] = Callable[Concatenate[HttpRequest, P], HttpResponseBase]


def get_authentik_groups(user: User | AnonymousUser) -> list[str]:
    """
    Get user's Authentik groups from UserGroups model.
    This is the single source of truth for permissions.
    """
    if isinstance(user, AnonymousUser):
        return []

    try:
        return list(user.usergroups.groups)
    except UserGroups.DoesNotExist:
        return []


def get_authentik_id(user: User) -> str | None:
    """Get user's Authentik user ID from UserGroups model."""
    try:
        return user.usergroups.authentik_id
    except UserGroups.DoesNotExist:
        return None


def get_permissions_context(user: User) -> dict[str, bool]:
    """Get permissions dict for template context.

    Auto-generates is_* flags from PERMISSION_MAP keys.
    """
    return {f"is_{perm}": has_permission(user, perm) for perm in PERMISSION_MAP}


def has_permission(user: User | AnonymousUser, permission_name: str) -> bool:
    """
    Check if user has a specific permission based on Authentik groups.

    Uses Authentik groups as source of truth.
    """
    groups = get_authentik_groups(user)
    return check_groups_for_permission(groups, permission_name)


def get_user_team_number(user: User) -> int | None:
    """
    Get user's team number from their Authentik group.
    Returns None if user is not on a team.
    """
    groups = get_authentik_groups(user)
    for group in groups:
        team_number = extract_team_number(group)
        if team_number is not None:
            return team_number
    return None


def get_role_based_landing_url(groups: list[str]) -> str:
    """Determine the landing page URL based on a user's Authentik groups.

    Checks roles in priority order: admin/ops first, then team-specific landing pages.
    Returns a URL path string. Falls back to "/" if no role matches.
    """
    from django.urls import reverse

    if (
        check_groups_for_permission(groups, "admin")
        or check_groups_for_permission(groups, "ticketing_admin")
        or check_groups_for_permission(groups, "ticketing_support")
    ):
        return reverse("ticket_list")
    if check_groups_for_permission(groups, "gold_team") or check_groups_for_permission(groups, "white_team"):
        return reverse("leaderboard_page")
    if check_groups_for_permission(groups, "red_team"):
        return reverse("scoring:submit_red_score")
    if check_groups_for_permission(groups, "orange_team"):
        return reverse("orange_team:dashboard")
    if check_groups_for_permission(groups, "blue_team"):
        return reverse("ticket_list")

    return "/"


def require_permission(
    *permission_names: str,
    error_message: str = "You don't have permission to access this page.",
    redirect_url: str = "/",
) -> Callable[[ViewFunc[P]], ViewFunc[P]]:
    """
    Decorator to require one or more permissions for a view.

    Grants access if user has ANY of the listed permissions.

    Usage:
        @require_permission('ticketing_admin')
        def my_view(request):
            ...

        @require_permission('red_team', 'gold_team')
        def multi_role_view(request):
            ...
    """
    from functools import wraps

    from django.contrib import messages
    from django.shortcuts import redirect

    def decorator(view_func: ViewFunc[P]) -> ViewFunc[P]:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args: P.args, **kwargs: P.kwargs) -> HttpResponseBase:
            if not request.user.is_authenticated or not any(
                has_permission(request.user, perm) for perm in permission_names
            ):
                messages.error(request, error_message)
                return redirect(redirect_url)
            return view_func(request, *args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator
