"""Admin views for competition management."""

import csv
import io
import json
import logging
from collections.abc import Iterator
from typing import cast

from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from scoring.models import QuotientMetadataCache
from scoring.quotient_sync import sync_quotient_metadata

from core.admin_views.readiness import action_readiness_check, action_readiness_fix
from core.authentik_manager import AuthentikManager
from core.authentik_utils import (
    generate_blueteam_password,
)
from core.forms import ActionForm, AppSlugForm, ResetPasswordsForm, SetMaxMembersForm, SetTimeForm
from core.models import AuditLog, CompetitionConfig, QueuedAnnouncement
from core.utils import ndjson_progress as _progress
from team.models import MAX_TEAMS

from ..auth_utils import has_permission, require_permission
from ..utils import parse_datetime_to_utc

logger = logging.getLogger(__name__)

TIMEZONE_CHOICES = [
    ("America/Los_Angeles", "Pacific Time (PT)"),
    ("America/Denver", "Mountain Time (MT)"),
    ("America/Chicago", "Central Time (CT)"),
    ("America/New_York", "Eastern Time (ET)"),
    ("UTC", "UTC"),
]


def _has_admin_or_gold_access(user: User) -> bool:
    """Check if user has admin or gold_team permission."""
    return has_permission(user, "admin") or has_permission(user, "gold_team")


def _action_set_max_members(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle set_max_members action."""
    from team.models import Team

    form = SetMaxMembersForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Max members must be 1-20"}, status=400)

    max_members = form.cleaned_data["max_members"]
    old_max = config.max_team_members
    config.max_team_members = max_members
    config.save()

    Team.objects.update(max_members=max_members)

    AuditLog.objects.create(
        action="max_team_members_updated",
        admin_user=authentik_username,
        target_entity="competition_config",
        target_id=config.pk,
        details={"old_max": old_max, "new_max": max_members},
    )

    return JsonResponse({"success": True, "message": f"Max members set to {max_members}"})


def _edit_controlled_app(
    request: HttpRequest, config: CompetitionConfig, authentik_username: str, *, add: bool
) -> JsonResponse:
    """Add or remove one controlled app against the CURRENT stored list.

    The page used to send its whole list, so an edit from a stale page (e.g. one that reloaded
    while another save was in flight) silently dropped apps added in between.
    """
    form = AppSlugForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Please provide an app slug"}, status=400)
    slug = form.cleaned_data["app_slug"]

    with transaction.atomic():
        config = CompetitionConfig.objects.select_for_update().get(pk=config.pk)
        apps = list(config.controlled_applications or [])
        if add and slug not in apps:
            apps.append(slug)
        elif not add and slug in apps:
            apps.remove(slug)
        config.controlled_applications = apps
        config.save(update_fields=["controlled_applications", "updated_at"])

        AuditLog.objects.create(
            action="competition_apps_configured",
            admin_user=authentik_username,
            target_entity="competition_config",
            target_id=config.pk,
            details={"controlled_apps": apps, "added" if add else "removed": slug},
        )

    verb = "Added" if add else "Removed"
    return JsonResponse({"success": True, "message": f"{verb} {slug}", "apps": apps})


def _action_add_app(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle add_app action."""
    return _edit_controlled_app(request, config, authentik_username, add=True)


def _action_remove_app(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle remove_app action."""
    return _edit_controlled_app(request, config, authentik_username, add=False)


def _action_set_start_time(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle set_start_time action."""
    form = SetTimeForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Please provide a datetime"}, status=400)

    datetime_str = form.cleaned_data["datetime"]
    tz_name = form.cleaned_data["timezone"]

    try:
        start_time = parse_datetime_to_utc(datetime_str, tz_name)

        if not config.controlled_applications:
            config.ensure_controlled_applications()

        config.competition_start_time = start_time
        config.applications_enabled = False
        config.save()

        AuditLog.objects.create(
            action="competition_start_time_set",
            admin_user=authentik_username,
            target_entity="competition_config",
            target_id=config.pk,
            details={"start_time": start_time.isoformat(), "controlled_apps": config.controlled_applications},
        )

        return JsonResponse({"success": True, "message": f"Start time set to {start_time.isoformat()}"})
    except ValueError:
        return JsonResponse({"error": "Invalid datetime format"}, status=400)


def _action_set_end_time(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle set_end_time action."""
    form = SetTimeForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Please provide a datetime"}, status=400)

    datetime_str = form.cleaned_data["datetime"]
    tz_name = form.cleaned_data["timezone"]

    try:
        end_time = parse_datetime_to_utc(datetime_str, tz_name)

        if not config.controlled_applications:
            config.ensure_controlled_applications()

        config.competition_end_time = end_time
        config.save()

        AuditLog.objects.create(
            action="competition_end_time_set",
            admin_user=authentik_username,
            target_entity="competition_config",
            target_id=config.pk,
            details={"end_time": end_time.isoformat(), "controlled_apps": config.controlled_applications},
        )

        return JsonResponse({"success": True, "message": f"End time set to {end_time.isoformat()}"})
    except ValueError:
        return JsonResponse({"error": "Invalid datetime format"}, status=400)


def _action_set_schedule(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle set_schedule action — set start and/or end time in one request."""
    from core.forms import SetScheduleForm

    form = SetScheduleForm(request.POST)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()), ["Invalid schedule data"])
        return JsonResponse({"error": str(first_error[0])}, status=400)

    start_dt = form.cleaned_data["start_datetime"]
    start_tz = form.cleaned_data.get("start_timezone") or "America/Los_Angeles"
    end_dt = form.cleaned_data["end_datetime"]
    end_tz = form.cleaned_data.get("end_timezone") or "America/Los_Angeles"

    try:
        details: dict[str, str] = {}

        if start_dt:
            start_time = parse_datetime_to_utc(start_dt, start_tz)
            config.competition_start_time = start_time
            config.applications_enabled = False
            details["start_time"] = start_time.isoformat()

        if end_dt:
            end_time = parse_datetime_to_utc(end_dt, end_tz)
            config.competition_end_time = end_time
            details["end_time"] = end_time.isoformat()

        if not config.controlled_applications:
            config.ensure_controlled_applications()

        config.save()

        AuditLog.objects.create(
            action="competition_schedule_set",
            admin_user=authentik_username,
            target_entity="competition_config",
            target_id=config.pk,
            details={**details, "controlled_apps": config.controlled_applications},
        )

        parts = [
            f"start={details['start_time']}" if "start_time" in details else "",
            f"end={details['end_time']}" if "end_time" in details else "",
        ]
        msg = "Schedule updated: " + ", ".join(p for p in parts if p)
        return JsonResponse({"success": True, "message": msg})
    except ValueError:
        return JsonResponse({"error": "Invalid datetime format"}, status=400)


def _stream_start_competition(config: CompetitionConfig, authentik_username: str) -> Iterator[str]:
    """Stream progress for starting the competition."""
    apps = config.controlled_applications
    total = len(apps) + MAX_TEAMS + 1  # apps + accounts + quotient sync
    auth_manager = AuthentikManager()

    # Phase 1: Enable applications
    app_ok = 0
    app_fail = 0
    for i, slug in enumerate(apps, 1):
        success, error = auth_manager.enable_application(slug)
        if success:
            app_ok += 1
            yield _progress(f"Enabled {slug}", i, total)
        else:
            app_fail += 1
            yield _progress(f"Failed {slug}: {error}", i, total, ok=False)

    # Phase 2: Enable team accounts
    acct_ok = 0
    acct_fail = 0
    for i in range(1, MAX_TEAMS + 1):
        username = f"team{i:02d}"
        success, _ = auth_manager.toggle_user(username, is_active=True)
        idx = len(apps) + i
        if success:
            acct_ok += 1
            yield _progress(f"Enabled {username}", idx, total)
        else:
            acct_fail += 1
            yield _progress(f"Failed {username}", idx, total, ok=False)

    # Phase 3: Quotient sync
    try:
        sync_quotient_metadata()
        quotient_synced = True
        yield _progress("Quotient metadata synced", total, total)
    except Exception as e:
        logger.warning(f"Failed to sync Quotient metadata: {e}")
        quotient_synced = False
        yield _progress(f"Quotient sync failed: {e}", total, total, ok=False)

    # Update config
    config.applications_enabled = True
    config.competition_start_time = None
    config.save()

    AuditLog.objects.create(
        action="competition_started",
        admin_user=authentik_username,
        target_entity="competition_config",
        target_id=config.pk,
        details={
            "apps_success": app_ok,
            "apps_failed": app_fail,
            "accounts_enabled": acct_ok,
            "accounts_failed": acct_fail,
            "quotient_synced": quotient_synced,
        },
    )

    quotient_msg = ", Quotient synced" if quotient_synced else ", Quotient sync failed"
    yield (
        json.dumps(
            {
                "done": True,
                "success": True,
                "message": (
                    f"Competition started. Apps: {app_ok}/{len(apps)}, Accounts: {acct_ok}/{MAX_TEAMS}{quotient_msg}"
                ),
            }
        )
        + "\n"
    )


def _action_start_competition(
    request: HttpRequest, config: CompetitionConfig, authentik_username: str
) -> StreamingHttpResponse:
    """Handle start_competition action with streaming progress."""
    if not config.controlled_applications:
        return StreamingHttpResponse(
            json.dumps({"done": True, "success": False, "message": "No controlled applications configured"}) + "\n",
            content_type="application/x-ndjson",
        )
    return StreamingHttpResponse(
        _stream_start_competition(config, authentik_username),
        content_type="application/x-ndjson",
    )


def _stream_stop_competition(config: CompetitionConfig, authentik_username: str) -> Iterator[str]:
    """Stream progress for stopping the competition."""
    apps = config.controlled_applications
    total = len(apps) + MAX_TEAMS  # apps + accounts
    auth_manager = AuthentikManager()

    # Phase 1: Disable applications
    app_ok = 0
    app_fail = 0
    for i, slug in enumerate(apps, 1):
        success, error = auth_manager.disable_application(slug)
        if success:
            app_ok += 1
            yield _progress(f"Disabled {slug}", i, total)
        else:
            app_fail += 1
            yield _progress(f"Failed {slug}: {error}", i, total, ok=False)

    # Phase 2: Disable team accounts
    acct_ok = 0
    acct_fail = 0
    for i in range(1, MAX_TEAMS + 1):
        username = f"team{i:02d}"
        success, _ = auth_manager.toggle_user(username, is_active=False)
        idx = len(apps) + i
        if success:
            acct_ok += 1
            yield _progress(f"Disabled {username}", idx, total)
        else:
            acct_fail += 1
            yield _progress(f"Failed {username}", idx, total, ok=False)

    # Update config
    config.applications_enabled = False
    config.competition_end_time = None
    config.save()

    AuditLog.objects.create(
        action="competition_stopped",
        admin_user=authentik_username,
        target_entity="competition_config",
        target_id=config.pk,
        details={
            "apps_disabled": app_ok,
            "apps_failed": app_fail,
            "accounts_disabled": acct_ok,
            "accounts_failed": acct_fail,
        },
    )

    yield (
        json.dumps(
            {
                "done": True,
                "success": True,
                "message": f"Competition stopped. Apps: {app_ok}/{len(apps)}, Accounts disabled: {acct_ok}/{MAX_TEAMS}",
            }
        )
        + "\n"
    )


def _action_stop_competition(
    request: HttpRequest, config: CompetitionConfig, authentik_username: str
) -> StreamingHttpResponse:
    """Handle stop_competition action with streaming progress."""
    if not config.controlled_applications:
        return StreamingHttpResponse(
            json.dumps({"done": True, "success": False, "message": "No controlled applications configured"}) + "\n",
            content_type="application/x-ndjson",
        )
    return StreamingHttpResponse(
        _stream_stop_competition(config, authentik_username),
        content_type="application/x-ndjson",
    )


def _action_cleanup_competition(
    request: HttpRequest, config: CompetitionConfig, authentik_username: str
) -> JsonResponse:
    """Handle cleanup_competition action."""
    from team.models import DiscordLink, Team

    if config.applications_enabled:
        return JsonResponse({"error": "Competition must be stopped before cleanup"}, status=400)

    # Deactivate team member links
    links = DiscordLink.objects.filter(is_active=True, team__isnull=False)
    deactivated = 0
    for link in links:
        link.is_active = False
        link.unlinked_at = timezone.now()
        link.save()
        deactivated += 1

        AuditLog.objects.create(
            action="user_unlinked",
            admin_user=authentik_username,
            target_entity="discord_link",
            target_id=link.discord_id,
            details={
                "discord_id": link.discord_id,
                "team_name": link.team.team_name if link.team else "Unknown",
                "authentik_username": link.user.username,
                "reason": "competition_cleanup",
            },
        )

    # Clear team Discord IDs
    Team.objects.all().update(discord_category_id=None, discord_role_id=None)

    # Clear competition times
    config.competition_start_time = None
    config.competition_end_time = None
    config.save()

    # Clear queued announcements
    deleted_count = QueuedAnnouncement.objects.all().delete()[0]

    # Clear Quotient metadata cache
    QuotientMetadataCache.objects.all().delete()

    AuditLog.objects.create(
        action="competition_cleanup",
        admin_user=authentik_username,
        target_entity="competition",
        target_id=0,
        details={
            "deactivated_links": deactivated,
            "cleared_announcements": deleted_count,
            "cleared_quotient_metadata": True,
        },
    )

    return JsonResponse(
        {
            "success": True,
            "message": f"Cleanup complete. Deactivated {deactivated} links, cleared {deleted_count} announcements, "
            "cleared Quotient metadata. Discord cleanup requires bot commands.",
        }
    )


def _action_wipe_competition(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle wipe_competition action - nuclear option to delete all competition data."""
    from core.competition_utils import wipe_competition_data

    if config.applications_enabled:
        return JsonResponse({"error": "Competition must be stopped before wiping"}, status=400)

    counts = wipe_competition_data()

    # Clear competition config times
    config.competition_start_time = None
    config.competition_end_time = None
    config.save()

    # Summarize what was deleted
    deleted_items = {k: v for k, v in counts.items() if v > 0}
    total_deleted = sum(counts.values())

    # Create a new audit log entry (after wiping, so it's the first entry)
    AuditLog.objects.create(
        action="competition_wiped",
        admin_user=authentik_username,
        target_entity="competition",
        target_id=0,
        details={"deleted_counts": deleted_items, "total_deleted": total_deleted},
    )

    summary_parts = [f"{v} {k}" for k, v in deleted_items.items()]
    summary = ", ".join(summary_parts) if summary_parts else "No data to delete"

    return JsonResponse(
        {
            "success": True,
            "message": f"Competition wiped! Deleted: {summary}",
        }
    )


def _action_reset_passwords(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle reset_passwords action."""
    form = ResetPasswordsForm(request.POST)
    if not form.is_valid():
        error_msg = "; ".join(str(e) for errors in form.errors.values() for e in errors)
        return JsonResponse({"error": error_msg}, status=400)

    team_numbers = form.cleaned_data["team_numbers"] or list(range(1, MAX_TEAMS + 1))

    auth_manager = AuthentikManager()
    password_list = []
    failed_resets = []

    for team_num in team_numbers:
        username = f"team{team_num:02d}"
        password = generate_blueteam_password()
        success, error = auth_manager.reset_blueteam_password(team_num, password)
        if success:
            password_list.append((team_num, username, password))
        else:
            failed_resets.append((username, error))

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["Username", "Password"])
    for _team_num, username, password in password_list:
        writer.writerow([username, password])

    csv_content = csv_buffer.getvalue()

    AuditLog.objects.create(
        action="blueteam_passwords_reset",
        admin_user=authentik_username,
        target_entity="authentik_users",
        target_id=0,
        details={
            "total_users": len(team_numbers),
            "success_count": len(password_list),
            "failed_count": len(failed_resets),
            "team_numbers": form.cleaned_data.get("team_numbers", "") or "all",
        },
    )

    return JsonResponse(
        {
            "success": True,
            "message": f"Reset {len(password_list)}/{len(team_numbers)} passwords",
            "csv": csv_content,
        }
    )


def _action_sync_quotient(request: HttpRequest, config: CompetitionConfig, authentik_username: str) -> JsonResponse:
    """Handle sync_quotient action."""
    try:
        sync_quotient_metadata()
        AuditLog.objects.create(
            action="quotient_metadata_synced",
            admin_user=authentik_username,
            target_entity="quotient",
            target_id=0,
            details={},
        )
        return JsonResponse({"success": True, "message": "Quotient metadata synced"})
    except Exception as e:
        logger.error(f"Failed to sync Quotient metadata: {e}")
        return JsonResponse({"error": f"Sync failed: {e}"}, status=500)


_COMPETITION_ACTION_HANDLERS = {
    "set_max_members": _action_set_max_members,
    "add_app": _action_add_app,
    "remove_app": _action_remove_app,
    "set_start_time": _action_set_start_time,
    "set_end_time": _action_set_end_time,
    "set_schedule": _action_set_schedule,
    "start_competition": _action_start_competition,
    "stop_competition": _action_stop_competition,
    "cleanup_competition": _action_cleanup_competition,
    "wipe_competition": _action_wipe_competition,
    "reset_passwords": _action_reset_passwords,
    "sync_quotient": _action_sync_quotient,
    "readiness_check": action_readiness_check,
    "readiness_fix": action_readiness_fix,
}


@require_permission("admin", "gold_team")
def admin_competition(request: HttpRequest) -> HttpResponse:
    """Competition management dashboard."""
    from team.models import DiscordLink, Team

    config = CompetitionConfig.get_config()

    # Get team counts
    active_teams = Team.objects.filter(is_active=True).count()
    total_teams = Team.objects.count()
    linked_users = DiscordLink.objects.filter(is_active=True, team__isnull=False).count()

    # Get available apps from Authentik
    auth_manager = AuthentikManager()
    available_apps = auth_manager.list_applications()

    # Get Quotient metadata
    quotient_metadata = QuotientMetadataCache.objects.first()

    context = {
        "config": config,
        "active_teams": active_teams,
        "total_teams": total_teams,
        "linked_users": linked_users,
        "available_apps": available_apps,
        "timezone_choices": TIMEZONE_CHOICES,
        "quotient_metadata": quotient_metadata,
        "show_ops_nav": True,
        "nav_active": "ops_admin",
    }

    return render(request, "admin/competition.html", context)


def admin_competition_action(request: HttpRequest) -> HttpResponseBase:
    """Handle competition management actions via dispatch."""
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    user = cast(User, request.user)
    if not _has_admin_or_gold_access(user):
        return JsonResponse({"error": "Access denied"}, status=403)

    form = ActionForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "No action specified"}, status=400)
    action = form.cleaned_data["action"]
    handler = _COMPETITION_ACTION_HANDLERS.get(action)
    if not handler:
        return JsonResponse({"error": "Unknown action"}, status=400)

    config = CompetitionConfig.get_config()
    return handler(request, config, user.username)


@require_permission("admin", "gold_team")
def admin_competition_danger(request: HttpRequest) -> HttpResponse:
    """Danger zone page for destructive competition operations."""
    config = CompetitionConfig.get_config()
    return render(
        request,
        "admin/competition_danger.html",
        {
            "config": config,
            "show_ops_nav": True,
            "nav_active": "ops_admin",
        },
    )
