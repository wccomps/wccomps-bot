"""Views for WCComps linking and OAuth."""

import logging
from typing import Protocol, cast

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from core.services.linking import (
    LinkResult,
    enforce_account_link_policy,
    execute_link,
    finalize_link,
    store_discord_id_in_authentik,
    validate_link_token,
)
from team.models import DiscordLink, LinkToken, SchoolInfo, Team

from .auth_utils import (
    get_authentik_groups,
    get_authentik_id,
    get_permissions_context,
    get_role_based_landing_url,
    require_permission,
)
from .forms import SchoolInfoEditForm
from .utils import get_team_from_groups


class _ManagerLike(Protocol):
    def exists(self) -> bool: ...


class ModelWithObjects(Protocol):
    objects: _ManagerLike
    __name__: str


logger = logging.getLogger(__name__)


def home(request: HttpRequest) -> HttpResponse:
    """Home page - redirect to appropriate dashboard based on user role."""
    user = cast(User, request.user)
    groups = get_authentik_groups(user)

    url = get_role_based_landing_url(groups)
    if url != "/":
        return redirect(url)

    # Blue team accounts go to ticket list; unknown roles to leaderboard
    _team, _, is_team = get_team_from_groups(groups)
    if is_team:
        return redirect("ticket_list")
    return redirect("leaderboard_page")


def link_initiate(request: HttpRequest) -> HttpResponse:
    """Initiate OAuth linking flow."""
    token = request.GET.get("token")

    if not token:
        return HttpResponse("Missing token parameter", status=400)

    # Validate token
    try:
        link_token = LinkToken.objects.get(token=token, used=False)
    except LinkToken.DoesNotExist:
        return render(
            request,
            "link_error.html",
            {
                "error": "Invalid or expired token",
                "message": "This link has expired or is invalid. Please use /link in Discord to generate a new one.",
            },
        )

    if link_token.is_expired():
        return render(
            request,
            "link_error.html",
            {
                "error": "Token expired",
                "message": (
                    "This link has expired (15 minute limit). Please use /link in Discord to generate a new one."
                ),
            },
        )

    # Store token in session for CSRF protection
    request.session["pending_link_token"] = link_token.token
    request.session["pending_link_discord_id"] = link_token.discord_id

    # Pass token through OAuth redirect via next parameter
    from urllib.parse import quote

    next_url = quote(f"/auth/link-callback?token={link_token.token}", safe="")
    return redirect(f"/auth/login/?next={next_url}")


def link_callback(request: HttpRequest) -> HttpResponse:
    """Handle OAuth callback after Authentik authentication."""
    # Clear any django-allauth success messages (we show our own)
    list(messages.get_messages(request))

    user = cast(User, request.user)
    try:
        authentik_username = user.username
        groups = get_authentik_groups(user)
        authentik_user_id = get_authentik_id(user)
    except Exception as e:
        logger.error(f"link_callback: Error getting Authentik data: {e}", exc_info=True)
        raise

    if not authentik_user_id:
        return render(
            request,
            "link_error.html",
            {
                "error": "Authentication error",
                "message": "Could not retrieve your Authentik account information.",
            },
        )

    def _render_error(result: LinkResult) -> HttpResponse:
        return render(request, cast(str, result.error_template), result.error_context)

    # Validate token from URL + session
    url_token = request.GET.get("token")
    session_token = request.session.get("pending_link_token")
    token_result = validate_link_token(url_token, session_token, authentik_username)
    if isinstance(token_result, LinkResult):
        return _render_error(token_result)
    link_token = token_result

    # Extract data from token
    discord_id = link_token.discord_id
    discord_username = link_token.discord_username

    # Get team information
    team, team_number, is_team_account = get_team_from_groups(groups)

    # Enforce one-to-one link policy for non-team accounts
    policy_error = enforce_account_link_policy(
        user, discord_id, discord_username, authentik_username, team, is_team_account
    )
    if policy_error:
        return _render_error(policy_error)

    # For non-team accounts, try to store discord_id in Authentik
    if not is_team_account:
        store_discord_id_in_authentik(authentik_username, discord_id)

    # Create or update DiscordLink (with race-condition protection for teams)
    link_error = execute_link(discord_id, discord_username, user, team, is_team_account)
    if link_error:
        return _render_error(link_error)

    # Mark token used, create audit records, queue Discord tasks
    finalize_link(
        link_token, discord_id, discord_username, authentik_username, team, team_number, is_team_account, groups
    )

    # Clear session data used for CSRF protection
    request.session.pop("pending_link_token", None)
    request.session.pop("pending_link_discord_id", None)

    return render(
        request,
        "link_success.html",
        {
            "team_name": f"Team {team.team_number}" if team else None,
            "team_number": team_number,
            "discord_username": discord_username,
            "authentik_username": authentik_username,
            "is_team_account": is_team_account,
        },
    )


@require_permission("gold_team")
def school_info(request: HttpRequest) -> HttpResponse:
    """View and edit school information (GoldTeam only)."""
    # Get all teams with their school info
    teams = Team.objects.filter(is_active=True).order_by("team_number")

    # Enrich teams with school info
    teams_with_info = []
    for team in teams:
        try:
            school_info = team.school_info
        except SchoolInfo.DoesNotExist:
            school_info = None

        teams_with_info.append({"team": team, "school_info": school_info})

    return render(
        request,
        "school_info.html",
        {
            "teams": teams_with_info,
            "show_ops_nav": True,
        },
    )


@require_permission("gold_team")
def school_info_export(request: HttpRequest) -> HttpResponse:
    """Export all school information as CSV."""
    import csv

    records = SchoolInfo.objects.select_related("team").order_by("team__team_number")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="school_info.csv"'

    writer = csv.writer(response)
    writer.writerow(["team_number", "team_name", "school_name", "contact_email", "secondary_email", "notes"])

    for si in records:
        writer.writerow(
            [
                si.team.team_number,
                si.team.team_name,
                si.school_name,
                si.contact_email,
                si.secondary_email or "",
                si.notes or "",
            ]
        )

    return response


@require_permission("gold_team")
def school_info_clear(request: HttpRequest) -> HttpResponse:
    """Clear all school information records."""
    count = SchoolInfo.objects.count()

    if request.method == "POST":
        from registration.models import EventTeamAssignment, TeamRegistration

        deleted, _ = SchoolInfo.objects.all().delete()
        EventTeamAssignment.objects.all().delete()
        TeamRegistration.objects.all().delete()
        logger.info(f"Cleared school info, registrations, and event assignments by {request.user.username}")
        messages.success(request, f"Cleared {deleted} school info record(s), registrations, and event assignments.")
        return redirect("school_info")

    return render(
        request,
        "school_info_clear.html",
        {"count": count, "show_ops_nav": True},
    )


@require_permission("gold_team")
def school_info_edit(request: HttpRequest, team_number: int) -> HttpResponse:
    """Edit school information for a team (GoldTeam only)."""
    user = cast(User, request.user)
    authentik_username = user.username

    # Get team
    try:
        team = Team.objects.get(team_number=team_number, is_active=True)
    except Team.DoesNotExist:
        return render(
            request,
            "error.html",
            {
                "error": "Team not found",
                "message": f"Team {team_number} does not exist.",
            },
        )

    # Get or create school info
    try:
        school_info = team.school_info
    except SchoolInfo.DoesNotExist:
        school_info = None

    if request.method == "POST":
        form = SchoolInfoEditForm(request.POST)
        if not form.is_valid():
            error_msg = " ".join(str(e) for errors in form.errors.values() for e in errors)
            return render(
                request,
                "school_info_edit.html",
                {
                    "team": team,
                    "school_info": school_info,
                    "error": error_msg,
                },
            )

        school_name = form.cleaned_data["school_name"]
        contact_email = form.cleaned_data["contact_email"]
        secondary_email = form.cleaned_data.get("secondary_email", "")
        notes = form.cleaned_data.get("notes", "")

        # Create or update school info
        if school_info:
            school_info.school_name = school_name
            school_info.contact_email = contact_email
            school_info.secondary_email = secondary_email
            school_info.notes = notes
            school_info.updated_by = authentik_username
            school_info.save()
        else:
            school_info = SchoolInfo.objects.create(
                team=team,
                school_name=school_name,
                contact_email=contact_email,
                secondary_email=secondary_email,
                notes=notes,
                updated_by=authentik_username,
            )

        logger.info(f"School info updated for Team {team_number} by {authentik_username}")

        return redirect("school_info")

    return render(
        request,
        "school_info_edit.html",
        {
            "team": team,
            "school_info": school_info,
            "show_ops_nav": True,
        },
    )


def _parse_school_info_csv(csv_file: UploadedFile[bytes]) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Parse and validate a school-info CSV, returning preview data and session rows.

    Returns:
        A tuple of (preview_data, session_rows).  preview_data is a dict suitable for the
        template (with errors, warnings, and optionally teams_to_create).  session_rows is
        the serialisable list to stash in the session (empty when there are errors).
    """
    from team.forms import parse_csv_file, validate_csv_data

    parse_result = parse_csv_file(csv_file)

    if parse_result["errors"]:
        return {
            "errors": parse_result["errors"],
            "warnings": parse_result["warnings"],
            "rows": [],
        }, []

    validation_result = validate_csv_data(parse_result["rows"])

    preview_data: dict[str, object] = {
        "errors": validation_result["errors"],
        "warnings": parse_result["warnings"] + validation_result["warnings"],
        "teams_to_create": validation_result["teams_to_create"],
        "can_import": not validation_result["errors"],
    }

    session_rows: list[dict[str, object]] = []
    if preview_data["can_import"]:
        session_rows = [
            {
                "team_number": row["team_number"],
                "school_name": row["school_name"],
                "contact_email": row["contact_email"],
                "secondary_email": row.get("secondary_email", ""),
                "notes": row.get("notes", ""),
                "team_name": row.get("team_name", ""),
            }
            for row in validation_result["teams_to_create"]
        ]

    return preview_data, session_rows


def _apply_school_info_import(
    import_data: dict[str, object], authentik_username: str
) -> tuple[dict[str, object] | None, dict[str, int] | None]:
    """Verify teams still exist and apply the CSV import.

    Returns:
        A tuple of (preview_data, import_results).  On validation errors preview_data is set
        and import_results is None.  On success preview_data is None and import_results
        contains created/assigned counts.
    """
    from team.forms import CSVRowData, apply_csv_import

    teams_to_create: list[CSVRowData] = import_data["teams_to_create"]  # type: ignore[assignment]

    team_numbers = [row["team_number"] for row in teams_to_create]
    teams_by_number: dict[int, Team] = {
        t.team_number: t for t in Team.objects.filter(team_number__in=team_numbers, is_active=True)
    }

    errors = []
    for row in teams_to_create:
        team_number = row["team_number"]
        if team_number not in teams_by_number:
            errors.append(f"Team {team_number} is no longer available")
        else:
            row["_team"] = teams_by_number[team_number]

    if errors:
        return {
            "errors": errors,
            "warnings": ["Please re-upload the CSV file."],
            "can_import": False,
        }, None

    result = apply_csv_import(teams_to_create, authentik_username)
    return None, {"created": result["created"], "assigned": result["assigned"]}


@require_permission("gold_team")
def school_info_import(request: HttpRequest) -> HttpResponse:
    """Import school information from CSV file (GoldTeam only)."""
    from team.forms import CSVUploadForm

    user = cast(User, request.user)
    authentik_username = user.username

    permissions = get_permissions_context(user)
    form = CSVUploadForm()
    preview_data: dict[str, object] | None = None
    import_results: dict[str, int] | None = None

    if request.method == "POST":
        if "upload" in request.POST:
            form = CSVUploadForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = cast(UploadedFile[bytes], request.FILES["csv_file"])
                preview_data, session_rows = _parse_school_info_csv(csv_file)
                if session_rows:
                    request.session["csv_import_data"] = {"teams_to_create": session_rows}

        elif "confirm" in request.POST:
            import_data = request.session.get("csv_import_data")
            if import_data:
                preview_data, import_results = _apply_school_info_import(import_data, authentik_username)
                if import_results is not None:
                    del request.session["csv_import_data"]

    return render(
        request,
        "school_info_import.html",
        {
            "authentik_username": authentik_username,
            "form": form,
            "preview_data": preview_data,
            "import_results": import_results,
            "show_ops_nav": True,
            **permissions,
        },
    )


@require_permission("gold_team")
def ops_group_role_mappings(request: HttpRequest) -> HttpResponse:
    """View team membership status and linked users (GoldTeam only)."""
    # Get all active teams with their linked members
    teams = Team.objects.filter(is_active=True).order_by("team_number")

    team_status = []
    for team in teams:
        # Get active links for this team
        links = DiscordLink.objects.filter(team=team, is_active=True).select_related("team")

        # Format member list
        members = [
            {
                "discord_id": link.discord_id,
                "discord_username": link.discord_username or "Unknown",
                "authentik_username": link.user.username,
            }
            for link in links
        ]

        team_status.append(
            {
                "team": team,
                "current_count": len(members),
                "max_count": team.max_members,
                "members": members,
                "is_full": len(members) >= team.max_members,
            }
        )

    from django.contrib.admin.sites import site

    context = {
        **site.each_context(request),
        "team_status": team_status,
        "title": "Team Mappings",
    }
    return render(request, "ops_group_role_mappings.html", context)


def health_check(request: HttpRequest) -> HttpResponse:
    """Health check endpoint for monitoring - tests database connectivity and model queries."""
    import json

    from django.apps import apps
    from django.db import connection

    errors = []

    # Check database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as e:
        errors.append(f"Database connection: {e!s}")

    # Check all models are queryable
    core_models = apps.get_app_config("core").get_models()
    for model_class in core_models:
        try:
            # Cast to ModelWithObjects protocol to access objects manager
            # django-stubs doesn't expose objects on type[Model]
            typed_model = cast("ModelWithObjects", model_class)
            typed_model.objects.exists()
        except Exception as e:
            errors.append(f"{model_class.__name__}: {str(e)[:100]}")

    if errors:
        return HttpResponse(
            json.dumps({"status": "unhealthy", "errors": errors}),
            content_type="application/json",
            status=503,
        )

    return HttpResponse(
        json.dumps({"status": "healthy"}),
        content_type="application/json",
        status=200,
    )
